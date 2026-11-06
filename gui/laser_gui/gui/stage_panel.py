# -*- coding: utf-8 -*-
"""SHRC-203 3軸ステージ制御パネル。

常時表示: 接続、現在位置 (論理座標 + 機械座標)。
タブ:
  [操作]         ジョグ (カメラ画像の向きに合わせた配置)、座標指定移動、
                 機械原点復帰 / 論理原点リセット、停止
  [位置登録]     センサーごとの登録位置辞書、登録原点 (機械座標) への移動、
                 形状から計算したストリップ位置への移動
  [アライメント] Z フォーカス / 原点出し / フル較正 (信号ベース)、
                 傾き補正係数の表示と手動入力

2026-10 の整理:
  - ジョグボタンは「押した矢印の向きにカメラ画像上で動く」配置にした
    (ステージ Y+ は画像の下向き。config.stage_screen_dir で変更可)
  - 位置登録は全センサー共通の 1 ファイルから、センサー (DUT) ごとの
    辞書 (sensors.py) へ移行。原点出しのたびに論理原点の機械座標を
    自動登録し、次回は「登録原点へ移動」で粗寄せできる
  - 原点出し・較正の操作 UI を自動スキャンパネルからここへ移動
  - タスク (スキャン・較正など) の実行中は手動移動を無効化する
    (スキャン中の誤ジョグ防止。停止ボタンは常に有効)
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

import sensors
from .alignment_worker import AlignmentWorker
from .autofocus_worker import AutofocusWorker
from .widgets import ElidedLabel
from .worker import AsyncCaller

# ジョグ1回あたりの移動量プリセット [µm]
# (2 µm/pulse のため整数パルスになる偶数刻みにする。1 µm は 0 pulse に
#  丸められて動かないので外した)
JOG_STEPS_UM = [2, 4, 10, 20, 50, 100, 500, 1000, 5000]

STRIP_PLACES = [("読み出し側端", "near"), ("中央", "mid"), ("反対側端", "far")]


class StagePanel(QGroupBox):
    POLL_MS = 500  # 位置ポーリング周期

    def __init__(self, stage, scope, laser, registry, runner, cfg,
                 parent=None):
        super().__init__("3軸ステージ (SHRC-203)", parent)
        self.stage = stage
        self.scope = scope
        self.laser = laser
        self.registry = registry
        self.runner = runner
        self.cfg = cfg
        self.caller = AsyncCaller("stage", self)
        self._log_dir = Path(cfg.log_dir)
        self._last_pos_um = [0.0, 0.0, 0.0]
        self._have_pos = False
        self._home_reminded = False
        self._manual_widgets: list[QWidget] = []   # タスク中に無効化する
        self._build_ui()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_MS)
        self._poll_timer.timeout.connect(self._poll_position)
        self._poll_pending = False

        registry.subscribe(self.refresh_sensor)
        runner.busy_changed.connect(self._on_busy_changed)
        self.refresh_sensor()

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- 接続行 ---
        conn_row = QHBoxLayout()
        default_addr = (self.stage.serial_port if self.stage.use_serial
                        else f"{self.stage.host}:{self.stage.port}")
        self.addr_edit = QLineEdit(default_addr)
        self.addr_edit.setToolTip("LAN: host:port / シリアル: COMポート名")
        self.connect_btn = QPushButton("接続")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.conn_label = QLabel("未接続")
        self.conn_label.setStyleSheet("color: gray;")
        conn_row.addWidget(QLabel("接続先:"))
        conn_row.addWidget(self.addr_edit, 1)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.conn_label)
        layout.addLayout(conn_row)

        # --- 現在位置表示 (論理座標) ---
        pos_row = QHBoxLayout()
        self.pos_labels = []
        for name in ("X", "Y", "Z"):
            lbl = QLabel(f"{name}: --- µm")
            lbl.setStyleSheet(
                "font-family: monospace; font-size: 13px; font-weight: bold;"
                "background: #263238; color: #aed581; padding: 2px;"
                "border-radius: 4px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.pos_labels.append(lbl)
            pos_row.addWidget(lbl)
        self.busy_label = QLabel("---")
        self.busy_label.setAlignment(Qt.AlignCenter)
        pos_row.addWidget(self.busy_label)
        layout.addLayout(pos_row)

        # --- 機械座標 (H:W 基準。R:W しても追跡する) ---
        self.machine_label = ElidedLabel("機械座標: ---")
        self.machine_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.machine_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_move_tab(), "操作")
        self.tabs.addTab(self._build_position_tab(), "位置登録")
        self.tabs.addTab(self._build_align_tab(), "アライメント")
        layout.addWidget(self.tabs)
        layout.addStretch(1)

        self._update_pulse_label()

    # ------------------------------------------------------------------
    # [操作] タブ
    # ------------------------------------------------------------------
    def _build_move_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("ステップ:"))
        self.step_combo = QComboBox()
        for um in JOG_STEPS_UM:
            label = f"{um} µm" if um < 1000 else f"{um / 1000:g} mm"
            self.step_combo.addItem(label, float(um))
        self.step_combo.setCurrentIndex(2)  # 10 µm
        step_row.addWidget(self.step_combo)
        self.pulse_label = QLabel("")  # 実際に送られるパルス数 (換算確認用)
        self.pulse_label.setStyleSheet("color: gray;")
        step_row.addWidget(self.pulse_label)
        step_row.addStretch(1)
        self.step_combo.currentIndexChanged.connect(self._update_pulse_label)
        layout.addLayout(step_row)

        def jog_btn(arrow, axis, sign):
            # ラベルは「画像上の向きの矢印 + 実際に送る軸と符号」
            name = f"{'XYZ'[axis]}{'+' if sign > 0 else '−'}"
            b = QPushButton(f"{arrow} {name}")
            b.setFixedSize(64, 30)
            b.clicked.connect(lambda: self._jog(axis, sign))
            b.setAutoRepeat(True)          # 長押しで連続ジョグ
            b.setAutoRepeatDelay(500)
            b.setAutoRepeatInterval(400)
            self._manual_widgets.append(b)
            return b

        # XY はカメラ画像の向きに合わせた十字配置: 押した矢印の方向へ
        # 画像上で動く。ステージ座標の正負は機器の取り付け向きで決まり、
        # 本機は「Y+ が画像の下向き」(X は右が +)。座標の符号に合わせた
        # 配置 (上=Y+) だと上下を取り違えやすかったため、画像基準にした。
        sx, sy = self.cfg.stage_screen_dir   # 画像の右 / 上 に対応する符号
        jog = QGridLayout()
        jog.addWidget(jog_btn("▲", 1, sy), 0, 1)
        jog.addWidget(jog_btn("◀", 0, -sx), 1, 0)
        jog.addWidget(jog_btn("▶", 0, sx), 1, 2)
        jog.addWidget(jog_btn("▼", 1, -sy), 2, 1)
        center = QLabel("カメラ\n画像の向き")
        center.setAlignment(Qt.AlignCenter)
        center.setStyleSheet("color: #546e7a; font-size: 10px;")
        jog.addWidget(center, 1, 1)
        jog.setColumnMinimumWidth(3, 16)
        jog.addWidget(jog_btn("▲", 2, +1), 0, 4)
        jog.addWidget(jog_btn("▼", 2, -1), 2, 4)
        jog.addWidget(QLabel("Z (フォーカス)"), 1, 4,
                      alignment=Qt.AlignCenter)
        jog.setColumnStretch(5, 1)
        layout.addLayout(jog)

        # --- 座標指定移動 (論理座標) ---
        abs_row = QHBoxLayout()
        # µm サフィックスは付けない: 6桁の座標値が見切れるため単位はラベルへ
        abs_row.addWidget(QLabel("座標指定 [µm]:"))
        self.abs_spins = []
        for name in ("X", "Y", "Z"):
            spin = QDoubleSpinBox()
            spin.setRange(-1e6, 1e6)
            spin.setDecimals(1)
            spin.setSingleStep(10)
            # 上下ボタンは省略: 6桁座標の表示幅を優先 (微調整はジョグで行う)
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            spin.setToolTip(f"{name} 目標位置 [µm] (論理座標)")
            self.abs_spins.append(spin)
            abs_row.addWidget(spin)
        go_btn = QPushButton("移動")
        go_btn.setMinimumWidth(56)
        go_btn.clicked.connect(self._move_absolute)
        self._manual_widgets.append(go_btn)
        abs_row.addWidget(go_btn)
        fill_btn = QPushButton("現在値")
        fill_btn.setMinimumWidth(56)
        fill_btn.setToolTip("現在位置を入力欄にコピー")
        fill_btn.clicked.connect(self._fill_current)
        abs_row.addWidget(fill_btn)
        layout.addLayout(abs_row)

        # --- 原点・停止 ---
        # 4 つ横並びだと最小幅 540 px で中カラム (~510 px) に収まらず横
        # スクロールが出るため 2×2 に折り返す
        ctrl_row = QGridLayout()
        home_btn = QPushButton("機械原点復帰 (H:W)")
        home_btn.setToolTip(
            "全軸を機械原点へ戻す。完了後は表示座標 = 機械座標になり、\n"
            "以後の論理原点リセット (R:W) を跨いで機械座標を追跡できる")
        home_btn.clicked.connect(self._home)
        zero_btn = QPushButton("論理原点リセット (R:W)")
        zero_btn.setToolTip("現在位置を論理原点 (0,0,0) にする (確認あり)")
        zero_btn.clicked.connect(self._zero)
        self._manual_widgets += [home_btn, zero_btn]
        stop_btn = QPushButton("停止 (L:W)")
        stop_btn.clicked.connect(
            lambda: self.caller.call(self.stage.stop))
        estop_btn = QPushButton("非常停止")
        estop_btn.setStyleSheet(
            "background: #b71c1c; color: white; font-weight: bold;")
        estop_btn.clicked.connect(
            lambda: self.caller.call(self.stage.emergency_stop))
        ctrl_row.addWidget(home_btn, 0, 0)
        ctrl_row.addWidget(zero_btn, 0, 1)
        ctrl_row.addWidget(stop_btn, 1, 0)
        ctrl_row.addWidget(estop_btn, 1, 1)
        layout.addLayout(ctrl_row)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # [位置登録] タブ
    # ------------------------------------------------------------------
    def _build_position_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self.pos_sensor_label = ElidedLabel("")
        self.pos_sensor_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.pos_sensor_label)

        # --- 登録原点 (論理原点の機械座標) ---
        self.origin_label = ElidedLabel("")
        self.origin_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.origin_label)
        origin_row = QHBoxLayout()
        goto_origin = QPushButton("登録原点へ移動")
        goto_origin.setToolTip(
            "このセンサーの登録原点 (機械座標) へ移動する。\n"
            "機械原点復帰 (H:W) 後の粗寄せに使う")
        goto_origin.clicked.connect(lambda: self._goto_origin(False))
        goto_start = QPushButton("原点出し開始位置へ")
        goto_start.setToolTip(
            "登録原点から基準ストリップ上・露出部の読み出し側端の\n"
            "100 µm 内側へ移動する (= 原点出しの開始位置)")
        goto_start.clicked.connect(lambda: self._goto_origin(True))
        reg_origin = QPushButton("現在の論理原点を登録")
        reg_origin.setToolTip(
            "現在の論理原点 (0,0,0) の機械座標をこのセンサーの原点として\n"
            "登録する (原点出しを実行すると自動で登録される)")
        reg_origin.clicked.connect(self._register_origin_manual)
        self._manual_widgets += [goto_origin, goto_start]
        origin_row.addWidget(goto_origin)
        origin_row.addWidget(goto_start)
        origin_row.addWidget(reg_origin)
        layout.addLayout(origin_row)

        # --- ストリップ位置 (形状 + 傾き係数から都度計算) ---
        strip_row = QHBoxLayout()
        self.strip_label = QLabel("ストリップへ移動:")
        strip_row.addWidget(self.strip_label)
        self.strip_ch_combo = QComboBox()
        strip_row.addWidget(self.strip_ch_combo)
        self.strip_place_combo = QComboBox()
        for label, key in STRIP_PLACES:
            self.strip_place_combo.addItem(label, key)
        strip_row.addWidget(self.strip_place_combo)
        strip_btn = QPushButton("移動")
        strip_btn.setToolTip(
            "選んだ CH の電極 (ストリップ: 電極中心線上 / ピクセル: パッド横・\n"
            "パッド中心・4 ピクセル交点) へ移動する。座標はセンサー形状と\n"
            "現在の傾き係数から計算する (論理原点が出ていることが前提)")
        strip_btn.clicked.connect(self._goto_strip)
        self._manual_widgets.append(strip_btn)
        strip_row.addWidget(strip_btn)
        strip_row.addStretch(1)
        layout.addLayout(strip_row)

        # --- 登録位置の辞書 ---
        bm_row = QHBoxLayout()
        self.bm_list = QListWidget()
        self.bm_list.setMinimumHeight(60)
        self.bm_list.setMaximumHeight(130)
        self.bm_list.itemDoubleClicked.connect(
            lambda _item: self._goto_bookmark())
        bm_btns = QVBoxLayout()
        self.frame_combo = QComboBox()
        self.frame_combo.addItem("論理座標で登録", "logical")
        self.frame_combo.addItem("機械座標で登録", "machine")
        self.frame_combo.setToolTip(
            "論理座標: センサーの論理原点基準。原点出しをやり直しても\n"
            "  センサー上の同じ場所を指す (通常はこちら)\n"
            "機械座標: 機械原点基準。原点を出す前の粗寄せ用")
        add_btn = QPushButton("現在位置を登録")
        add_btn.clicked.connect(self._add_bookmark)
        goto_btn = QPushButton("選択位置へ移動")
        goto_btn.clicked.connect(self._goto_bookmark)
        self._manual_widgets.append(goto_btn)
        del_btn = QPushButton("削除")
        del_btn.clicked.connect(self._delete_bookmark)
        bm_btns.addWidget(self.frame_combo)
        bm_btns.addWidget(add_btn)
        bm_btns.addWidget(goto_btn)
        bm_btns.addWidget(del_btn)
        bm_btns.addStretch(1)
        bm_row.addWidget(self.bm_list, 1)
        bm_row.addLayout(bm_btns)
        layout.addLayout(bm_row)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # [アライメント] タブ
    # ------------------------------------------------------------------
    def _build_align_tab(self) -> QWidget:
        """信号ベースの Z フォーカス・原点出し・フル較正の操作 UI。

        いずれもレーザー ON で実行し、オシロの振幅 (PAVA) を位置センサー
        として使う (gui/autofocus_worker.py, gui/alignment_worker.py)。
        ★焦点が合うほど振幅は小さくなる (LGAD ゲイン抑制、2026-09-07
        実測) ため、Z フォーカスは「シリコン上で振幅最小 = 谷の中心」★
        """
        w = QWidget()
        layout = QVBoxLayout(w)

        grid = QGridLayout()
        grid.addWidget(QLabel("CH:"), 0, 0)
        self.af_ch = QComboBox()
        self.af_ch.setToolTip("振幅を読むオシロ CH (原点出しは基準 CH の"
                              "ストリップで行う)")
        grid.addWidget(self.af_ch, 0, 1)
        grid.addWidget(QLabel("Z範囲 ±"), 0, 2)
        self.af_range = QDoubleSpinBox()
        self.af_range.setRange(4.0, 500.0)
        self.af_range.setValue(20.0)
        self.af_range.setSuffix(" µm")
        self.af_range.setDecimals(0)
        grid.addWidget(self.af_range, 0, 3)
        grid.addWidget(QLabel("step"), 0, 4)
        self.af_step = QDoubleSpinBox()
        self.af_step.setRange(2.0, 50.0)   # Z 分解能 2 µm/pulse
        self.af_step.setValue(2.0)
        self.af_step.setSuffix(" µm")
        self.af_step.setDecimals(0)
        grid.addWidget(self.af_step, 0, 5)
        self.af_metric = QComboBox()
        self.af_metric.addItem("振幅最小 (焦点)", "min")
        self.af_metric.addItem("振幅最大 (旧)", "max")
        self.af_metric.setToolTip(
            "焦点が合うほど振幅は小さくなる (LGAD ゲイン抑制。2026-09-07\n"
            "実測で確定)。既定の「振幅最小」は、信号が明瞭に見える\n"
            "ストリップ横のシリコン上にビームを置いて実行する\n"
            "(浅い谷の中心 = 焦点)。電極直上は振幅 0 のまま谷が見えない\n"
            "ので不可。「振幅最大」は旧方式で焦点に合わない")
        grid.addWidget(self.af_metric, 1, 0, 1, 3)
        self.af_btn = QPushButton("Z フォーカス")
        self.af_btn.setToolTip(
            "現在位置で Z を掃引し、振幅が極値の Z へ移動する。\n"
            "レーザー ON・ストリップ横のシリコン上で実行すること")
        self.af_btn.clicked.connect(self._start_autofocus)
        grid.addWidget(self.af_btn, 1, 3, 1, 3)
        layout.addLayout(grid)

        box = QGroupBox("自動アライメント (信号ベース)")
        box.setToolTip(
            "事前準備: ビームを基準 CH のストリップ上、露出部の読み出し側端\n"
            "から 50〜200 µm 内側に置く (「原点出し開始位置へ」で粗寄せ可)。\n"
            "レーザー ON・トリガー EXT で信号が見えている状態で実行する")
        bgrid = QGridLayout(box)
        self.align_laser_check = QCheckBox("開始時にレーザー自動 ON")
        bgrid.addWidget(self.align_laser_check, 0, 0)
        self.align_premove_check = QCheckBox("論理座標の開始位置へ自動移動")
        self.align_premove_check.setToolTip(
            "ON: 開始前に、現在の論理原点を基準に基準ストリップ上・露出部の\n"
            "読み出し側端から 100 µm 内側へ移動してから始める。\n"
            "原点出し済みの状態 (原点 = 外枠エッジで信号なし) から続けて\n"
            "フル較正や原点出しのやり直しをするときに使う。\n"
            "OFF: 現在位置から始める (カメラ等で粗寄せした直後)")
        bgrid.addWidget(self.align_premove_check, 0, 1)
        self.align_fine_check = QCheckBox("精密仕上げ+検証 (推奨, 時間 2〜3 倍)")
        self.align_fine_check.setChecked(True)
        self.align_fine_check.setToolTip(
            "ON (推奨): 粗検出の後に 1 パルス (2 µm) 刻み・多数回読み (中央値) の\n"
            "精密掃引を行い、電極中心 Y・信号エッジ X を erf フィット、焦点 Z を\n"
            "ローレンツ型フィットでサブ µm に決める。往復掃引でバックラッシュも\n"
            "推定し、R:W の後に原点を再測定して残差が 1 パルス以上の軸があれば\n"
            "補正して R:W をやり直す (最大 2 回)。掃引記録は logs/alignment_<日時>.csv\n"
            "所要: 原点出し 約 7〜12 分、フル較正 約 15〜25 分\n"
            "OFF: 従来の粗検出のみ (原点出し 2〜3 分、フル較正 8〜12 分)")
        bgrid.addWidget(self.align_fine_check, 1, 0, 1, 2)
        self.align_origin_btn = QPushButton("原点出し (Z→Y→X→R:W)")
        self.align_origin_btn.setToolTip(
            "①Y掃引で電極位置の当たり ②電極横のシリコン上で振幅最小の\n"
            "Z (=焦点) ③Y精密掃引で電極中心 ④X掃引で信号エッジ検出\n"
            "⑤Z再確認 → 外枠エッジ相当で論理原点リセット (R:W)\n"
            "⑥(精密仕上げ+検証 ON) R:W 後に原点を再測定し 1 パルス以上の\n"
            "残差を補正。完了時に論理原点の機械座標をこのセンサーへ自動登録する。\n"
            "約 2〜3 分 (精密仕上げ+検証 ON では約 7〜12 分)")
        self.align_origin_btn.clicked.connect(
            lambda: self._start_alignment("origin"))
        bgrid.addWidget(self.align_origin_btn, 2, 0)
        self.align_full_btn = QPushButton("フル較正 (原点+傾き係数)")
        self.align_full_btn.setToolTip(
            "原点出しの後、下記の X/Y 測定点で電極中心とフォーカスを実測し、\n"
            "dY/dX・dZ/dX・dZ/dY をフィットしてこのセンサーに保存する。\n"
            "約 8〜12 分 (精密仕上げ+検証 ON では約 15〜25 分)。\n"
            "載せ替え・治具変更のときに実行する")
        self.align_full_btn.clicked.connect(
            lambda: self._start_alignment("full"))
        bgrid.addWidget(self.align_full_btn, 2, 1)
        self.align_points_label = QLabel("")
        self.align_points_label.setStyleSheet("color: #546e7a;")
        self.align_points_label.setWordWrap(True)
        bgrid.addWidget(self.align_points_label, 3, 0, 1, 2)
        layout.addWidget(box)

        coef_row = QHBoxLayout()
        self.calib_label = QLabel("")
        self.calib_label.setStyleSheet("color: #546e7a;")
        self.calib_label.setWordWrap(True)
        coef_row.addWidget(self.calib_label, 1)
        self.coef_btn = QPushButton("係数を手動入力...")
        self.coef_btn.setToolTip(
            "傾き補正係数 (dZ/dX, dZ/dY, dY/dX) を手で入力して保存する")
        self.coef_btn.clicked.connect(self._edit_coefficients)
        coef_row.addWidget(self.coef_btn)
        layout.addLayout(coef_row)
        layout.addStretch(1)
        self._task_buttons = [self.af_btn, self.align_origin_btn,
                              self.align_full_btn, self.coef_btn]
        return w

    # ------------------------------------------------------------------
    # センサー切り替え・状態反映
    # ------------------------------------------------------------------
    def refresh_sensor(self):
        """現在のセンサープロファイルに合わせて表示を更新する。"""
        prof = self.registry.current()
        chans = sensors.used_channels(prof)
        pixel = sensors.is_pixel(prof)
        self.pos_sensor_label.setText(
            f"センサー: {prof['name']}" + (" [ピクセル型]" if pixel else ""))
        # 「電極へ移動」の選択肢はセンサー種類で変わる
        self.strip_label.setText("ピクセルへ移動:" if pixel else "ストリップへ移動:")
        keep_place = self.strip_place_combo.currentData()
        self.strip_place_combo.blockSignals(True)
        self.strip_place_combo.clear()
        for label, key in self.registry.places():
            self.strip_place_combo.addItem(label, key)
        idx = self.strip_place_combo.findData(keep_place)
        self.strip_place_combo.setCurrentIndex(max(idx, 0))
        self.strip_place_combo.blockSignals(False)

        for combo in (self.af_ch, self.strip_ch_combo):
            keep = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            for ch in chans:
                pad = prof["readout"][ch].get("pad", "")
                combo.addItem(f"{ch} ({pad})" if pad else ch, ch)
            idx = combo.findData(keep.split()[0] if keep else "")
            combo.setCurrentIndex(max(idx, 0))
            combo.blockSignals(False)

        org = prof.get("machine_origin")
        if org:
            x, y, z = org["pos_um"]
            self.origin_label.setText(
                f"登録原点 (機械座標): X={x:.0f} Y={y:.0f} Z={z:.0f} µm — "
                f"{str(org.get('registered_at', ''))[:16]} "
                f"{org.get('method', '')}")
        else:
            self.origin_label.setText(
                "登録原点: なし (原点出しを実行すると自動登録されます)")

        self.bm_list.clear()
        for name, entry in self.registry.positions().items():
            x, y, z = entry["pos_um"]
            tag = "論理" if entry.get("frame") == "logical" else "機械"
            item = QListWidgetItem(
                f"{name}  [{tag}] X={x:.1f} Y={y:.1f} Z={z:.1f}")
            item.setData(Qt.UserRole, name)
            self.bm_list.addItem(item)

        al = prof["align"]
        if pixel:
            pads = ", ".join(f"{ch} ({x:.0f}, {y:.0f})" for ch, x, y in al.get("pads", []))
            self.align_points_label.setText(
                f"フル較正の測定点 (パッド中心) — {pads or 'なし'} µm "
                "(センサー編集で変更可)")
        else:
            xs = ", ".join(f"{x:.0f}" for x in al.get("x_points", []))
            ys = ", ".join(f"{y:.0f} ({ch})" for y, ch in al.get("y_points", []))
            self.align_points_label.setText(
                f"フル較正の測定点 — X: {xs or 'なし'} µm / Y: {ys or 'なし'} µm "
                "(センサー編集で変更可)")
        self.calib_label.setText(
            f"傾き補正係数: {self.registry.summary()}")

    def _on_busy_changed(self, busy: bool):
        for w in self._manual_widgets + self._task_buttons:
            w.setEnabled(not busy)

    # ------------------------------------------------------------------
    # 接続
    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.stage.connected:
            self._poll_timer.stop()
            self.caller.call(self.stage.disconnect,
                             on_result=lambda _: self._on_disconnected())
            return
        addr = self.addr_edit.text().strip()
        if ":" in addr and not addr.upper().startswith("COM") \
                and not addr.startswith("/dev/"):
            host, _, port = addr.partition(":")
            self.stage.use_serial = False
            self.stage.host = host
            try:
                self.stage.port = int(port) if port else 9004
            except ValueError:
                QMessageBox.warning(self, "入力エラー",
                                    f"ポート番号が不正です: {port!r}")
                return
        else:
            self.stage.use_serial = True
            self.stage.serial_port = addr
        self.connect_btn.setEnabled(False)
        self.caller.call(self.stage.connect,
                         on_result=lambda _: self._on_connected(),
                         on_error=self._on_conn_error)

    def _on_connected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("切断")
        self.conn_label.setText("接続中")
        self.conn_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        # 速度プロファイルを適用 (コントローラ既定は低速で、原点復帰後の
        # センサー位置への長距離移動が遅すぎるため。台形駆動なので目標
        # 付近では自動減速し停止精度は変わらない)
        if self.cfg.stage_speed_pps is not None:
            s, f, r = self.cfg.stage_speed_pps
            self.caller.call(lambda: self.stage.set_speed(s, f, r),
                             on_error=self._show_error)
        self._poll_timer.start()
        # 起動直後は原点復帰が必要 (仕様書の必須操作)。セッション中 1 回だけ促す
        if not self._home_reminded:
            self._home_reminded = True
            QMessageBox.information(
                self, "原点復帰の確認",
                "SHRC-203 は起動直後に機械原点復帰が必要です。\n"
                "未実施の場合は「機械原点復帰 (H:W)」を実行してください。\n"
                "(GUI から実行すると機械座標の追跡も始まり、登録原点への\n"
                " 移動と原点の自動登録が使えるようになります)")

    def _on_disconnected(self):
        self.connect_btn.setText("接続")
        self.conn_label.setText("未接続")
        self.conn_label.setStyleSheet("color: gray;")
        self._have_pos = False

    def _on_conn_error(self, exc: Exception):
        self.connect_btn.setEnabled(True)
        QMessageBox.warning(self, "ステージ接続エラー", str(exc))

    # ------------------------------------------------------------------
    # 位置ポーリング
    # ------------------------------------------------------------------
    def _poll_position(self):
        if self._poll_pending or not self.stage.connected:
            return
        self._poll_pending = True
        self.caller.call(self.stage.get_position_um,
                         on_result=self._update_position,
                         on_error=lambda e: setattr(self, "_poll_pending", False))

    def _update_position(self, result):
        self._poll_pending = False
        pos_um, busy = result
        self._last_pos_um = pos_um
        self._have_pos = True
        for lbl, name, um in zip(self.pos_labels, "XYZ", pos_um):
            lbl.setText(f"{name}: {um:,.1f} µm")
        mpos = self.stage.machine_from_logical(pos_um)
        if mpos is None:
            self.machine_label.setText(
                "機械座標: 不明 (GUI から機械原点復帰 (H:W) すると追跡開始)")
        else:
            self.machine_label.setText(
                "機械座標: X={:,.0f} Y={:,.0f} Z={:,.0f} µm "
                "(機械原点基準)".format(*mpos))
        if busy:
            self.busy_label.setText("移動中")
            self.busy_label.setStyleSheet("color: #ef6c00; font-weight: bold;")
        else:
            self.busy_label.setText("Ready")
            self.busy_label.setStyleSheet("color: #2e7d32;")

    def last_position(self) -> list[float] | None:
        """直近のポーリングで得た論理座標 (未取得なら None)。"""
        return list(self._last_pos_um) if self._have_pos else None

    # ------------------------------------------------------------------
    # 移動操作
    # ------------------------------------------------------------------
    def _update_pulse_label(self):
        um = self.step_combo.currentData()
        pulses = [self.stage.um_to_pulse(i, um) for i in range(3)]
        self.pulse_label.setText(
            f"= {pulses[0]}/{pulses[1]}/{pulses[2]} pulse (X/Y/Z)")

    def _manual_ok(self) -> bool:
        """手動移動してよい状態か (接続済み・タスク非実行)。"""
        return self.stage.connected and not self.runner.is_busy()

    def _jog(self, axis: int, sign: int):
        if not self._manual_ok():
            return
        um = self.step_combo.currentData() * sign
        self.caller.call(lambda: self.stage.move_relative_um(axis, um),
                         on_error=self._show_error)

    def _goto_logical(self, pos):
        """論理座標 pos へ移動する (スキャンと同じ相対移動 M:W 方式)。"""
        if not self._manual_ok():
            return
        x, y, z = pos
        self.caller.call(lambda: self.stage.move_to_um_relative(x, y, z),
                         on_error=self._show_error)

    def _move_absolute(self):
        self._goto_logical([s.value() for s in self.abs_spins])

    def _fill_current(self):
        for spin, um in zip(self.abs_spins, self._last_pos_um):
            spin.setValue(um)

    def _home(self):
        # 原点復帰は全軸が大きく動くため確認を挟む (対物レンズとの衝突防止)
        ret = QMessageBox.question(
            self, "機械原点復帰",
            "全軸が機械原点まで移動します。\n"
            "サンプル・対物レンズと干渉しないことを確認しましたか?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes and self._manual_ok():
            self.caller.call(self.stage.home_all, on_error=self._show_error)

    def _zero(self):
        if not self._manual_ok():
            return
        # R:W は取り消せない (現在の論理原点を失う) ので確認を挟む
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("論理原点リセット (R:W)")
        box.setText("現在位置を論理原点 (0,0,0) にします。\n"
                    "これまでの論理原点は失われます。実行しますか?")
        chk = QCheckBox(f"この位置を「{self.registry.current()['name']}」の"
                        "原点として登録する")
        box.setCheckBox(chk)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        register = chk.isChecked()

        def done(_):
            if register:
                self._register_origin("手動 R:W")
        self.caller.call(self.stage.zero_all, on_result=done,
                         on_error=self._show_error)

    def _show_error(self, exc: Exception):
        QMessageBox.warning(self, "ステージエラー", str(exc))

    # ------------------------------------------------------------------
    # 登録原点 / ストリップ位置
    # ------------------------------------------------------------------
    def _register_origin(self, method: str) -> bool:
        """現在の論理原点の機械座標をセンサーへ登録する (不明なら False)。"""
        off = self.stage.machine_offset_um
        if off is None:
            return False
        self.registry.set_machine_origin(off, method)
        return True

    def _register_origin_manual(self):
        if not self._register_origin("手動登録"):
            QMessageBox.warning(
                self, "登録できません",
                "機械座標を追跡できていません。GUI から機械原点復帰 (H:W) を"
                "実行してから原点を出し直してください。")

    def _goto_origin(self, to_start: bool):
        prof = self.registry.current()
        org = prof.get("machine_origin")
        if not org:
            QMessageBox.warning(self, "移動できません",
                                "このセンサーの原点が未登録です")
            return
        target = list(org["pos_um"])
        desc = "登録原点"
        if to_start and sensors.is_pixel(prof):
            pad = float(prof.get("pad_um", 100.0))
            target[0] -= pad / 2.0
            target[1] -= pad / 2.0
            desc = "原点出し開始位置 (基準ピクセルのパッド中心)"
        elif to_start:
            target[0] -= float(prof["x_frame_offset_um"]) + 100.0
            desc = "原点出し開始位置 (基準ストリップ上・端から 100 µm 内側)"
        logical = self.stage.logical_from_machine(target)
        if logical is None:
            QMessageBox.warning(
                self, "移動できません",
                "機械座標を追跡できていません。GUI から機械原点復帰 (H:W) を"
                "実行してください。")
            return
        ret = QMessageBox.question(
            self, "登録位置へ移動",
            f"{prof['name']} の{desc}へ移動します。\n"
            "機械座標 X={:.0f} Y={:.0f} Z={:.0f} µm\n".format(*target)
            + "(登録 {} {})\n".format(str(org.get("registered_at", ""))[:16],
                                     org.get("method", ""))
            + "センサーを載せ替えた場合は位置がずれている可能性があります。\n"
              "対物レンズと干渉しないことを確認しましたか?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._goto_logical(logical)

    def _goto_strip(self):
        ch = self.strip_ch_combo.currentData()
        if ch is None:
            return
        self._goto_logical(self.registry.position_for(
            ch, self.strip_place_combo.currentData()))

    # ------------------------------------------------------------------
    # 登録位置の辞書 (センサーごと)
    # ------------------------------------------------------------------
    def _selected_bookmark(self) -> str | None:
        item = self.bm_list.currentItem()
        return None if item is None else item.data(Qt.UserRole)

    def _add_bookmark(self):
        if not self.stage.connected:
            QMessageBox.warning(self, "登録できません",
                                "ステージが未接続です")
            return
        frame = self.frame_combo.currentData()
        if frame == "machine" and self.stage.machine_offset_um is None:
            QMessageBox.warning(
                self, "登録できません",
                "機械座標を追跡できていません。GUI から機械原点復帰 "
                "(H:W) を実行するか、論理座標で登録してください。")
            return
        name, ok = QInputDialog.getText(self, "位置登録", "名前:")
        name = name.strip()
        if not ok or not name:
            return
        if name in self.registry.positions():
            ret = QMessageBox.question(
                self, "上書き確認", f"「{name}」は登録済みです。上書きしますか?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return

        # 表示中の座標は最大 0.5 秒古い (ポーリング値) ので、登録時に
        # ステージから読み直す。移動中なら登録しない
        def register(result):
            pos, busy = result
            if busy:
                QMessageBox.warning(self, "登録できません",
                                    "ステージ移動中です。停止後に登録して"
                                    "ください")
                return
            if frame == "machine":
                pos = self.stage.machine_from_logical(pos)
                if pos is None:
                    return
            self.registry.set_position(name, pos, frame)
        self.caller.call(self.stage.get_position_um, on_result=register,
                         on_error=self._show_error)

    def _goto_bookmark(self):
        name = self._selected_bookmark()
        entry = self.registry.positions().get(name) if name else None
        if entry is None:
            return
        target = list(entry["pos_um"])
        if entry.get("frame") != "logical":
            target = self.stage.logical_from_machine(target)
            if target is None:
                QMessageBox.warning(
                    self, "移動できません",
                    "機械座標で登録された位置です。機械座標を追跡できて"
                    "いないため移動できません (GUI から H:W を実行)。")
                return
        self._goto_logical(target)

    def _delete_bookmark(self):
        name = self._selected_bookmark()
        if name is None:
            return
        ret = QMessageBox.question(
            self, "削除確認", f"登録位置「{name}」を削除しますか?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self.registry.delete_position(name)

    # ------------------------------------------------------------------
    # Z オートフォーカス (波形振幅ベース)
    # ------------------------------------------------------------------
    def _task_precheck(self, need_laser: bool = False) -> bool:
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "実行不可",
                f"「{self.runner.current_name()}」の実行中です")
            return False
        if not (self.scope.connected and self.stage.connected):
            QMessageBox.warning(self, "実行不可",
                                "オシロとステージの接続が必要です")
            return False
        if need_laser and not self.laser.connected:
            QMessageBox.warning(self, "実行不可",
                                "レーザー自動 ON にはレーザー接続が必要です")
            return False
        return True

    def _start_autofocus(self):
        if not self._task_precheck():
            return
        ch = self.af_ch.currentData()
        metric = self.af_metric.currentData()
        if metric == "min":
            desc = (f"Z を ±{self.af_range.value():.0f} µm 掃引して {ch} の"
                    "振幅が最小の Z (= 焦点、谷の中心) へ移動します。\n"
                    "確認: (1) レーザー ON (2) ビームがストリップ横の"
                    "シリコン上 — 信号が明瞭に見える位置に置く\n"
                    f"(電極直上は不可) (3) {ch} で信号が見えている "
                    "(トリガー EXT)")
        else:
            desc = (f"Z を ±{self.af_range.value():.0f} µm 掃引して {ch} の"
                    "振幅が最大の Z へ移動します (旧方式)。\n"
                    "★ 焦点で振幅は最小になるため、この方式は焦点に"
                    "合わない可能性が高いです ★")
        ret = QMessageBox.question(
            self, "Z オートフォーカス",
            desc + "\nシーケンスは OFF・トリガーは NORM に切り替わります "
            "(スキャン開始時に再設定されるので問題ありません)。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {"ch": ch, "range_um": self.af_range.value(),
                  "step_um": self.af_step.value(), "metric": metric}
        worker = AutofocusWorker(self.scope, self.stage, params,
                                 self._log_dir / f"autofocus_{stamp}.csv")
        self.runner.start("Z フォーカス", worker, worker.finished_focus,
                          self._on_task_finished("オートフォーカス"))

    def _on_task_finished(self, title: str):
        def done(ok: bool, message: str, _worker):
            if ok:
                QMessageBox.information(self, f"{title}完了", message)
            else:
                QMessageBox.warning(self, f"{title}終了", message)
        return done

    # ------------------------------------------------------------------
    # 自動アライメント (信号ベース)
    # ------------------------------------------------------------------
    def _start_alignment(self, mode: str):
        laser_on = self.align_laser_check.isChecked()
        if not self._task_precheck(need_laser=laser_on):
            return
        prof = self.registry.current()
        al = prof["align"]
        ch = self.af_ch.currentData()
        ref = prof["ref_channel"]
        x_off = float(prof["x_frame_offset_um"])
        if sensors.is_pixel(prof):
            return self._start_alignment_pixel(mode, prof, al, ch, ref, laser_on)
        lines = [
            f"信号ベース自動アライメントを開始します "
            f"(センサー: {prof['name']} / CH: {ch})。",
            "",
            "確認事項:",
            f"  1. ビームが {ref} ストリップ上、露出部の読み出し側端から",
            "     50〜200 µm 内側にあること (カメラで粗寄せ済み)",
            "  2. レーザー ON (または自動 ON 指定)・トリガー EXT で",
            f"     {ch} に信号が見えていること",
            "",
            "手順: ①Y粗掃引で電極検出 ②電極横のシリコン上で振幅最小の",
            "Z (=焦点) ③Y精密掃引 ④X信号エッジ検出 ⑤Z再確認 →",
            f"エッジ+{x_off:.0f} µm (外枠エッジ相当) で論理原点リセット (R:W)。",
            "完了時に論理原点の機械座標をこのセンサーへ登録します。",
        ]
        if self.align_premove_check.isChecked():
            lines[3:5] = [
                f"  1. 現在の論理原点が正しいこと: 開始前に論理座標 "
                f"X={-(x_off + 100.0):.0f}, Y=0",
                f"     ({ref} ストリップ上・端から 100 µm 内側) へ自動移動します"]
        if ch != ref:
            lines.insert(1, f"★ 読み出し CH ({ch}) が基準 CH ({ref}) と"
                            "違います。意図どおりか確認してください ★")
        fine = self.align_fine_check.isChecked()
        if fine:
            lines += [
                "精密仕上げ+検証 ON: 粗検出の後に 1 パルス刻み・多数回読みの",
                "精密掃引 (erf / ローレンツ型フィット、往復でバックラッシュ推定)",
                "を行い、R:W 後に原点を再測定して 1 パルス以上の残差を補正します。",
            ]
        if mode == "full":
            xs = ", ".join(f"{x:.0f}" for x in al["x_points"])
            ys = ", ".join(f"{y:.0f} ({c})" for y, c in al["y_points"])
            lines += [
                f"⑥ X = {xs} µm と Y = {ys} µm で電極中心+フォーカスを",
                "   実測し、傾き係数 (dY/dX, dZ/dX, dZ/dY) を更新 "
                + ("(約 15〜25 分)" if fine else "(約 8〜12 分)"),
                "★ ステージがストリップ全長を移動します。",
                "  干渉物がないことを確認してください ★",
            ]
        else:
            lines.append("(所要 約 7〜12 分)" if fine else "(所要 約 2〜3 分)")
        lines.append("シーケンスは OFF・トリガーは NORM に切り替わります。")
        ret = QMessageBox.question(
            self, "自動アライメントの確認", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {
            "mode": mode,
            "ch": ch,
            "af_range_um": self.af_range.value(),
            "af_step_um": self.af_step.value(),
            "laser_on": laser_on,
            "x_frame_offset_um": x_off,
            "pitch_um": float(prof["pitch_um"]),
            "x_points": list(al["x_points"]),
            "y_points": [list(v) for v in al["y_points"]],
            "y_probe_off_um": float(al["y_probe_off_um"]),
            "x_probe_off_um": float(al["x_probe_off_um"]),
            "y_half_um": float(al["y_half_um"]),
            "coef0": self.registry.coefficients(),
            # 精密仕上げ+検証 (gui/alignment_worker.py のモジュール docstring)
            "fine": fine, "verify": fine, "backlash_check": fine,
            "csv_path": str(self._log_dir / f"alignment_{stamp}.csv"),
        }
        if self.align_premove_check.isChecked():
            params["pre_move_xy"] = (-(x_off + 100.0), 0.0)
        worker = AlignmentWorker(self.scope, self.stage, self.laser, params)
        name = "フル較正" if mode == "full" else "原点出し"
        self.runner.start(name, worker, worker.finished_align,
                          self._on_alignment_finished)

    def _start_alignment_pixel(self, mode: str, prof: dict, al: dict,
                               ch: str, ref: str, laser_on: bool):
        """ピクセル型の原点出し / フル較正 (gui/alignment_worker.py の「ピクセル型」)。"""
        pad = float(prof.get("pad_um", 100.0))
        pitch = float(prof["pitch_um"])
        fine = self.align_fine_check.isChecked()
        lines = [
            f"信号ベース自動アライメント (ピクセル型) を開始します "
            f"(センサー: {prof['name']} / CH: {ch})。",
            "",
            "確認事項:",
            f"  1. ビームが {ref} のボンディングパッド (無信号の {pad:.0f} µm 角) の",
            "     上または近く (±50 µm) にあること (カメラで粗寄せ済み)",
            "  2. レーザー ON (または自動 ON 指定)・トリガー EXT で",
            f"     パッドの外で {ch} に信号が見えていること",
            "",
            "手順: ①Y掃引でパッドのディップ検出 ②パッド横のシリコン上で振幅最小の",
            "Z (=焦点) ③Y精密ディップ ④X掃引でパッド中心 X ⑤Z再確認 →",
            f"パッド中心 + ({pad / 2:.0f}, {pad / 2:.0f}) µm = パッドの角で論理原点リセット (R:W)。",
            f"ピクセル中心は (−{pad / 2:.0f}, −{pad / 2:.0f})、隣のピクセルは ±{pitch:.0f} µm。",
            "完了時に論理原点の機械座標をこのセンサーへ登録します。",
        ]
        if self.align_premove_check.isChecked():
            lines[3:5] = [
                f"  1. 現在の論理原点が正しいこと: 開始前に論理座標 "
                f"X={-pad / 2:.0f}, Y={-pad / 2:.0f} (基準パッドの中心) へ自動移動します"]
        if ch != ref:
            lines.insert(1, f"★ 読み出し CH ({ch}) が基準 CH ({ref}) と"
                            "違います。意図どおりか確認してください ★")
        if fine:
            lines += [
                "精密仕上げ+検証 ON: 粗検出の後に 1 パルス刻み・多数回読みの",
                "精密掃引 (erf / ローレンツ型フィット、往復でバックラッシュ推定)",
                "を行い、R:W 後に原点を再測定して 1 パルス以上の残差を補正します。",
            ]
        pads = [list(v) for v in al.get("pads", [])]
        if mode == "full":
            pl = ", ".join(f"{c} ({x:.0f}, {y:.0f})" for c, x, y in pads)
            lines += [
                f"⑥ 他の読み出しピクセルのパッド中心 {pl or 'なし'} µm で",
                "   同じ測定 (その CH で読む) を行い、平面フィットで傾き係数を更新",
                "   (レバーアーム 1 ピッチ: 係数は粗い目安)",
            ]
            if not pads:
                QMessageBox.warning(self, "フル較正できません",
                                    "基準以外の読み出しピクセルがありません "
                                    "(センサー編集で読み出し CH を確認してください)")
                return
        else:
            lines.append("(所要 約 7〜12 分)" if fine else "(所要 約 2〜3 分)")
        lines.append("シーケンスは OFF・トリガーは NORM に切り替わります。")
        ret = QMessageBox.question(
            self, "自動アライメントの確認 (ピクセル)", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {
            "mode": mode,
            "sensor_type": "pixel",
            "ch": ch,
            "af_range_um": self.af_range.value(),
            "af_step_um": self.af_step.value(),
            "laser_on": laser_on,
            "pad_um": pad,
            "pitch_um": pitch,
            "pads": pads,
            "x_points": [], "y_points": [],
            "y_probe_off_um": float(al.get("y_probe_off_um", pad / 2.0 + 80.0)),
            "x_probe_off_um": 0.0,
            "y_half_um": float(al.get("y_half_um", pad + 100.0)),
            "coef0": self.registry.coefficients(),
            "fine": fine, "verify": fine, "backlash_check": fine,
            "csv_path": str(self._log_dir / f"alignment_{stamp}.csv"),
        }
        if self.align_premove_check.isChecked():
            params["pre_move_xy"] = (-pad / 2.0, -pad / 2.0)
        worker = AlignmentWorker(self.scope, self.stage, self.laser, params)
        name = "フル較正 (ピクセル)" if mode == "full" else "原点出し (ピクセル)"
        self.runner.start(name, worker, worker.finished_align,
                          self._on_alignment_finished)

    def _on_alignment_finished(self, ok: bool, message: str, worker):
        # 結果の保存は GUI スレッドで行う (現在のセンサープロファイルへ)
        notes = []
        if worker.origin_done:
            if self._register_origin("自動アライメント"):
                x, y, z = self.stage.machine_offset_um
                notes.append(f"論理原点の機械座標を登録しました: "
                             f"X={x:.0f} Y={y:.0f} Z={z:.0f} µm")
            else:
                notes.append("機械座標が不明なため原点の登録は省略しました "
                             "(GUI から H:W を実行すると次回から登録されます)")
        if worker.calibration is not None:
            path = self.registry.set_calibration(worker.calibration)
            notes.append(f"係数を「{self.registry.current()['name']}」に"
                         f"保存しました (履歴: {path.name})")
        self.refresh_sensor()
        text = "\n".join([message] + notes)
        if ok:
            QMessageBox.information(self, "自動アライメント完了", text)
        else:
            QMessageBox.warning(self, "自動アライメント終了", text)

    def _edit_coefficients(self):
        """傾き補正係数の手動入力ダイアログ (単位 µm/mm)。"""
        dzdx, dzdy, dydx, source = self.registry.coefficients()
        dlg = QDialog(self)
        dlg.setWindowTitle("傾き補正係数の手動入力")
        form = QFormLayout(dlg)
        form.addRow(QLabel(f"センサー: {self.registry.current()['name']}\n"
                           f"現在の出典: {source}"))
        spins = []
        for label, value, tip in (
                ("dZ/dX [µm/mm]", dzdx, "X に沿ったフォーカス面の傾き"),
                ("dZ/dY [µm/mm]", dzdy, "Y に沿ったフォーカス面の傾き"),
                ("dY/dX [µm/mm]", dydx, "ストリップの面内回転 "
                                         "(X に沿った電極中心 Y のドリフト)")):
            spin = QDoubleSpinBox()
            spin.setRange(-100.0, 100.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setValue(value * 1000.0)
            spin.setToolTip(tip)
            form.addRow(label, spin)
            spins.append(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() == QDialog.Accepted:
            self.registry.set_manual_coefficients(
                *(s.value() / 1000.0 for s in spins))

    # ------------------------------------------------------------------
    def emergency_stop(self):
        if self.stage.connected:
            self.caller.call(self.stage.emergency_stop)

    def shutdown(self):
        self._poll_timer.stop()
        if self.stage.connected:
            try:
                self.stage.disconnect()
            except Exception:
                pass
        self.caller.shutdown()
