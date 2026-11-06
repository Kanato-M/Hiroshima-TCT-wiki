# -*- coding: utf-8 -*-
"""自動スキャンパネル (Phase 3)。

スキャン条件の入力 → 測定点数/イベント数の見積もり表示 → 実行。
実行本体は scan_worker.ScanWorker (別スレッド)。進捗表示と中断は
ツールバーのタスク表示 (gui/task_runner.py) に集約している。

座標は論理原点基準 [µm] (外枠エッジ X=0・基準 CH の電極中心 Y=0・
合焦 Z=0)。原点はステージパネルの [アライメント] タブの「原点出し」で
決める。ステージは相対位置移動 (M:W) で動く。

スキャン範囲の既定値・傾き補正係数は現在のセンサープロファイル
(sensors.py) から来る。センサーを切り替えると既定範囲が入れ替わる。

センサー傾き補正 (1d/2d):
  回転追従 (Y): ストリップの面内回転 dY/dX に合わせて Y をシフト
  Z 追従: 次の 3 方式から選ぶ (scan_worker.build_points 参照)
    - 較正係数 (論理原点で合焦): フル較正 / 手動入力の係数で作る平面を
      論理原点に固定。原点出し直後の標準。開始位置に依存しない
    - 較正係数 (現在位置で合焦): 同じ平面を開始時の現在位置に固定
      (手動でフォーカスを合わせた場所から始めるとき)
    - 実測 Z (始点・終点 / 四隅): スキャン範囲の端で実測した合焦 Z を
      線形 / 双線形補間する。係数の較正が要らない簡易で確実な方式

付属機能: データ回収 (オシロ → PC, SSH/SFTP。gui/transfer_worker.py)。
Z フォーカス・自動アライメントはステージパネル、TUNE 最適化は
レーザーパネルへ移動した (2026-10)。
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from devices.scope_scp import ScpFetcher, host_from_resource

from .scan_worker import ScanWorker, build_points, count_points, scan_corners
from .transfer_worker import TransferWorker
from .widgets import ElidedLabel
from .worker import AsyncCaller

AXIS_ITEMS = [("X (1軸)", 0), ("Y (2軸)", 1), ("Z (3軸)", 2)]

Z_MODES = [
    ("較正係数 (論理原点で合焦)", "coef_origin"),
    ("較正係数 (現在位置で合焦)", "coef_current"),
    ("実測 Z (始点・終点 / 四隅)", "points"),
    ("なし", "off"),
]
Z_MODE_TIPS = (
    "Z (フォーカス) の追従方式:\n"
    "・較正係数 (論理原点で合焦): 傾き係数の平面を論理原点 (0,0,0) に固定。\n"
    "  原点出し直後の標準。スキャン開始時にステージがどこにあっても同じ\n"
    "・較正係数 (現在位置で合焦): 平面を開始時の現在位置に固定。\n"
    "  手動でフォーカスを合わせた位置から始める場合\n"
    "・実測 Z: スキャン範囲の始点・終点 (2D は四隅) で合焦 Z を実測して\n"
    "  入力し、その間を線形 (双線形) 補間する。係数の較正が不要\n"
    "・なし: Z を動かさない")


class ScanPanel(QGroupBox):
    def __init__(self, scope, stage, laser, registry, runner, cfg,
                 get_stage_pos=None, parent=None):
        super().__init__("自動スキャン (Phase 3)", parent)
        self.scope = scope
        self.stage = stage
        self.laser = laser
        self.registry = registry
        self.runner = runner
        self.cfg = cfg
        self._log_dir = Path(cfg.log_dir)
        # ステージパネルが周期取得している現在位置 (論理座標) を借りる
        self._get_stage_pos = get_stage_pos or (lambda: None)
        self.caller = AsyncCaller("scanaux", self)
        self._applied_defaults = None   # 入力欄へ反映済みの (センサー, 既定範囲)
        # 自動で入れた日付フォルダ (日付が変わったら追従させるため控える)
        self._auto_save_dir = cfg.dated_save_dir()
        self._build_ui(self._auto_save_dir)
        registry.subscribe(self.refresh_sensor)
        runner.busy_changed.connect(self._on_busy_changed)
        self.refresh_sensor()
        self._mode_changed()

    # ------------------------------------------------------------------
    def _build_ui(self, scope_save_dir: str):
        # タブ: [スキャン条件] [データ回収] (縦の必要量を減らす)
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        scan_tab = QWidget()
        tabs.addTab(scan_tab, "スキャン条件")
        layout = QVBoxLayout(scan_tab)

        # --- モード選択 ---
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("モード:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("1D 位置スキャン", "1d")
        self.mode_combo.addItem("2D ラスタースキャン", "2d")
        self.mode_combo.addItem("1D + TUNE (各点でエネルギースキャン)",
                                "1d_tune")
        self.mode_combo.addItem("TUNE スキャン (固定位置)", "tune")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        self.defaults_btn = QPushButton("センサー既定範囲")
        self.defaults_btn.setToolTip(
            "スキャン範囲を現在のセンサーの既定値に戻す")
        self.defaults_btn.clicked.connect(
            lambda: self._apply_scan_defaults(force=True))
        mode_row.addWidget(self.defaults_btn)
        layout.addLayout(mode_row)

        # 論理原点方式の注意書き (位置スキャンの前提条件)
        origin_note = ElidedLabel(
            "座標は論理原点基準 (外枠エッジ X=0・基準電極中心 Y=0・合焦 Z=0)。"
            "原点はステージパネルの [アライメント] →「原点出し」で決める。")
        origin_note.setStyleSheet("color: #546e7a;")
        layout.addWidget(origin_note)

        # 列構成: 0=行ラベル, 1=軸選択, 2=開始, 3=終了, 4=ステップ。
        # 開始/終了/ステップは列見出しにして行ごとのラベルを省き、狭い
        # 論理解像度 (高DPIモニター) でも座標6桁が見切れない幅を確保する。
        grid = QGridLayout()
        for col, text in ((2, "開始"), (3, "終了"), (4, "ステップ")):
            head = QLabel(text)
            head.setAlignment(Qt.AlignCenter)
            head.setStyleSheet("color: #546e7a;")
            grid.addWidget(head, 0, col)
            grid.setColumnStretch(col, 1)

        def dspin(lo, hi, val, step=1.0, suffix="", dec=1):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSingleStep(step)
            s.setSuffix(suffix)
            s.setDecimals(dec)
            s.valueChanged.connect(self._update_estimate)
            return s

        # --- 高速軸 (毎点動かす軸) ---
        grid.addWidget(QLabel("高速軸 [µm]:"), 1, 0)
        self.fast_axis = QComboBox()
        for label, idx in AXIS_ITEMS:
            self.fast_axis.addItem(label, idx)
        self.fast_axis.setCurrentIndex(1)  # Y = ストリップ横断
        self.fast_axis.currentIndexChanged.connect(self._update_estimate)
        grid.addWidget(self.fast_axis, 1, 1)
        self.fast_start = dspin(-1e6, 1e6, 0.0)
        self.fast_stop = dspin(-1e6, 1e6, 0.0)
        self.fast_step = dspin(0.1, 1e5, 10.0)
        grid.addWidget(self.fast_start, 1, 2)
        grid.addWidget(self.fast_stop, 1, 3)
        grid.addWidget(self.fast_step, 1, 4)

        # --- 低速軸 (2D の行方向) ---
        self.slow_label = QLabel("低速軸 [µm]:")
        grid.addWidget(self.slow_label, 2, 0)
        self.slow_axis = QComboBox()
        for label, idx in AXIS_ITEMS:
            self.slow_axis.addItem(label, idx)
        self.slow_axis.setCurrentIndex(0)  # 既定: 高速Y (横断) × 低速X (長手)
        self.slow_axis.currentIndexChanged.connect(self._update_estimate)
        grid.addWidget(self.slow_axis, 2, 1)
        self.slow_start = dspin(-1e6, 1e6, 0.0)
        self.slow_stop = dspin(-1e6, 1e6, 0.0)
        self.slow_step = dspin(0.1, 1e5, 500.0)
        grid.addWidget(self.slow_start, 2, 2)
        grid.addWidget(self.slow_stop, 2, 3)
        grid.addWidget(self.slow_step, 2, 4)
        self.snake_check = QCheckBox("蛇行 (往復) スキャン")
        self.snake_check.setChecked(True)
        grid.addWidget(self.snake_check, 3, 1, 1, 2)

        # --- TUNE スキャン (固定位置エネルギースキャン: 50–90%) ---
        self.tune_label = QLabel("TUNE [%]:")
        grid.addWidget(self.tune_label, 4, 0)
        self.tune_start = dspin(0, 100, 50.0, 5.0)
        self.tune_stop = dspin(0, 100, 90.0, 5.0)
        self.tune_step = dspin(0.1, 100, 5.0, 1.0)
        grid.addWidget(self.tune_start, 4, 2)
        grid.addWidget(self.tune_stop, 4, 3)
        grid.addWidget(self.tune_step, 4, 4)
        layout.addLayout(grid)

        layout.addWidget(self._build_tilt_box())

        # --- 取得条件 ---
        grid = QGridLayout()
        grid.addWidget(QLabel("整定時間:"), 0, 0)
        self.settle_spin = dspin(0.0, 60.0, 0.5, 0.1, " s", 1)
        grid.addWidget(self.settle_spin, 0, 1)
        self.seq_check = QCheckBox("シーケンス")
        self.seq_check.setChecked(True)
        self.seq_check.stateChanged.connect(self._update_estimate)
        grid.addWidget(self.seq_check, 0, 2)
        self.segments_spin = QSpinBox()
        self.segments_spin.setRange(2, 1000000)
        self.segments_spin.setValue(2000)   # プランの 2000 events/step
        self.segments_spin.setToolTip("1点あたりのセグメント数 (=イベント数)")
        self.segments_spin.valueChanged.connect(self._update_estimate)
        grid.addWidget(self.segments_spin, 0, 3)
        self.timeout_spin = dspin(1.0, 3600.0, 60.0, 10.0, " s", 0)
        self.timeout_spin.setToolTip("タイムアウト: 1測定点あたりの取得完了待ちの上限")
        timeout_label = QLabel("タイムアウト:")
        timeout_label.setToolTip("1測定点あたりの取得完了待ちの上限")
        grid.addWidget(timeout_label, 1, 0)
        grid.addWidget(self.timeout_spin, 1, 1)
        self.avg_off_check = QCheckBox("アベレージング OFF")
        self.avg_off_check.setChecked(True)
        self.avg_off_check.setToolTip(
            "スキャン開始時に C1–C4 の Averaging を OFF (AverageSweeps=1) に"
            "する。\nリアルタイム波形にしないと SINGLE/シーケンス取得の完了"
            "判定が機能しないため、\n通常は ON (チェック) のままにすること")
        grid.addWidget(self.avg_off_check, 1, 2, 1, 2)

        # --- 保存設定 (オシロ側) ---
        save_label = QLabel("保存先:")
        save_label.setToolTip(
            "オシロ本体側の保存ベースディレクトリ。\n"
            "スキャン毎にこの下へ scan_<日時>_<モード> サブフォルダを"
            "自動作成して保存する")
        grid.addWidget(save_label, 2, 0)
        self.save_dir_edit = QLineEdit(scope_save_dir)
        grid.addWidget(self.save_dir_edit, 2, 1, 1, 2)
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("prefix:"))
        self.prefix_edit = QLineEdit("scan")
        prefix_row.addWidget(self.prefix_edit)
        grid.addLayout(prefix_row, 2, 3)

        # --- レーザー自動制御 ---
        self.laser_on_check = QCheckBox("開始時にレーザー ON")
        self.laser_off_check = QCheckBox("終了時にレーザー OFF")
        self.laser_off_check.setChecked(True)
        grid.addWidget(self.laser_on_check, 3, 0, 1, 2)
        grid.addWidget(self.laser_off_check, 3, 2, 1, 2)
        layout.addLayout(grid)

        # --- 見積もり・実行 (進捗はツールバーのタスク表示に出る) ---
        self.estimate_label = ElidedLabel("---")
        self.estimate_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.estimate_label)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("スキャン開始")
        self.start_btn.setStyleSheet(
            "background: #1565c0; color: white; font-weight: bold; padding: 6px;")
        self.start_btn.clicked.connect(self._start_scan)
        self.abort_btn = QPushButton("中断")
        self.abort_btn.setToolTip("実行中のタスクを中断する "
                                  "(ツールバーの「中断」と同じ)")
        self.abort_btn.setStyleSheet(
            "QPushButton { background: #b71c1c; color: white;"
            " font-weight: bold; padding: 6px; }"
            "QPushButton:disabled { background: #cfd8dc; color: #90a4ae; }")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.runner.abort)
        btn_row.addWidget(self.start_btn, 3)
        btn_row.addWidget(self.abort_btn, 1)
        layout.addLayout(btn_row)
        layout.addStretch(1)
        tabs.addTab(self._build_transfer_tab(), "データ回収")

    def _build_tilt_box(self) -> QGroupBox:
        """センサー傾き補正 (回転追従 Y / Z 追従) の設定 UI。"""
        box = QGroupBox("傾き補正")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        z_label = QLabel("Z 追従:")
        z_label.setToolTip(Z_MODE_TIPS)
        row.addWidget(z_label)
        self.z_mode_combo = QComboBox()
        for label, key in Z_MODES:
            self.z_mode_combo.addItem(label, key)
        self.z_mode_combo.setToolTip(Z_MODE_TIPS)
        self.z_mode_combo.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.z_mode_combo, 1)
        self.rot_check = QCheckBox("回転追従 (Y)")
        self.rot_check.setChecked(True)
        self.rot_check.setToolTip(
            "ストリップの XY 面内回転 (dY/dX) に合わせて、X スキャン開始位置を\n"
            "基準に Y をシフトする。ON にするとどの X 行でもストリップが同じ\n"
            "スキャン Y 座標に現れる (X をスキャンしないときは補正量 0)")
        self.rot_check.stateChanged.connect(self._update_estimate)
        row.addWidget(self.rot_check)
        v.addLayout(row)

        # 現在有効な係数 (センサー切り替え・フル較正・手動入力のたびに更新)
        self.coef_label = QLabel("")
        self.coef_label.setWordWrap(True)
        self.coef_label.setStyleSheet("color: #546e7a;")
        v.addWidget(self.coef_label)

        # 実測 Z 方式の入力欄: 始点・終点 (1D) / 四隅 (2D)
        self.z_points_widget = QWidget()
        pg = QGridLayout(self.z_points_widget)
        pg.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("各位置へ移動 → フォーカスを合わせる (Z ジョグ / "
                      "Z フォーカス) →「現在Z」で取り込む")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #546e7a;")
        pg.addWidget(hint, 0, 0, 1, 4)
        self.z_point_rows = []
        for i in range(4):
            label = ElidedLabel("")
            spin = QDoubleSpinBox()
            spin.setRange(-1e5, 1e5)
            spin.setDecimals(1)
            spin.setSingleStep(2.0)
            spin.setSuffix(" µm")
            spin.setToolTip("この位置での合焦 Z (論理座標)")
            spin.valueChanged.connect(self._update_estimate)
            go = QPushButton("移動")
            go.setToolTip("この位置へ XY だけ移動する (Z は現在のまま)")
            go.clicked.connect(lambda _=False, k=i: self._goto_z_point(k))
            grab = QPushButton("現在Z")
            grab.setToolTip("ステージの現在の Z を取り込む")
            grab.clicked.connect(lambda _=False, k=i: self._grab_z_point(k))
            pg.addWidget(label, i + 1, 0)
            pg.addWidget(spin, i + 1, 1)
            pg.addWidget(go, i + 1, 2)
            pg.addWidget(grab, i + 1, 3)
            self.z_point_rows.append((label, spin, go, grab))
        pg.setColumnStretch(0, 1)
        v.addWidget(self.z_points_widget)
        return box

    def _build_transfer_tab(self) -> QWidget:
        """データ回収 (オシロ → PC) の操作 UI (SSH/SFTP, scp 相当)。

        指定日付のフォルダ <scope_save_dir>\\<年>\\<YYYYMMDD> を丸ごと
        PC 側 <保存先>\\<年>\\<YYYYMMDD> へ回収する (devices/scope_scp.py)。
        回収済みファイル/フォルダは自動スキップ。パスワードは転送のたびに
        入力する (設定ファイルには保存しない)。進捗表示はスキャンと共用。
        """
        box = QWidget()
        grid = QGridLayout(box)
        user = getattr(self.cfg, "scope_smb_user", "<SCOPE_USER>")
        host = host_from_resource(self.scope.resource)
        src = QLabel(f"回収元: {user}@{host}:"
                     f"{self.cfg.scope_save_dir}\\<年>\\<日付> "
                     "(パスワードは転送時に入力)")
        src.setStyleSheet("color: #546e7a;")
        src.setWordWrap(True)
        grid.addWidget(src, 0, 0, 1, 3)
        grid.addWidget(QLabel("日付 (YYYYMMDD):"), 1, 0)
        self.transfer_date_edit = QLineEdit(
            datetime.date.today().strftime("%Y%m%d"))
        self.transfer_date_edit.setToolTip(
            "回収する日付フォルダ。既定は今日。過去の日付も指定できる")
        grid.addWidget(self.transfer_date_edit, 1, 1, 1, 2)
        grid.addWidget(QLabel("PC側保存先:"), 2, 0)
        self.transfer_dest_edit = QLineEdit(
            str(Path(self.cfg.transfer_dest_dir).resolve()))
        self.transfer_dest_edit.setToolTip(
            "この下に <年>\\<日付>\\ が作られる (例: ...\\data\\2026\\20260907)")
        grid.addWidget(self.transfer_dest_edit, 2, 1, 1, 2)
        self.transfer_delete_check = QCheckBox(
            "転送後にオシロ側を削除 (全件のサイズ検証成功時のみ)")
        self.transfer_delete_check.setChecked(False)
        self.transfer_delete_check.setToolTip(
            "回収後、PC 側とサイズが一致したファイルだけオシロ側から削除する\n"
            "(容量確保)。1件でも失敗/競合/中断があれば削除は行わない。\n"
            "既定 OFF: コピーのみ (重複フォルダは次回自動スキップされる)")
        grid.addWidget(self.transfer_delete_check, 3, 0, 1, 2)
        self.transfer_btn = QPushButton("転送開始")
        self.transfer_btn.clicked.connect(self._start_transfer)
        grid.addWidget(self.transfer_btn, 3, 2)
        grid.setRowStretch(4, 1)
        return box

    # ------------------------------------------------------------------
    # センサー切り替え・状態反映
    # ------------------------------------------------------------------
    def refresh_sensor(self):
        """センサー / 係数の変更を表示へ反映する (registry からの通知)。"""
        self.coef_label.setText(f"係数: {self.registry.summary()}")
        self._apply_scan_defaults(force=False)
        self._update_estimate()

    def _apply_scan_defaults(self, force: bool):
        """現在のセンサーの既定スキャン範囲を入力欄へ入れる。

        通知のたびに上書きすると手入力が消えるので、センサーが変わった
        (または既定範囲そのものが編集された) ときだけ反映する。
        """
        prof = self.registry.current()
        key = (prof["id"], tuple(sorted(prof["scan"].items())))
        if not force and key == self._applied_defaults:
            return
        self._applied_defaults = key
        sc = prof["scan"]
        for spin, name in ((self.fast_start, "fast_start"),
                           (self.fast_stop, "fast_stop"),
                           (self.fast_step, "fast_step"),
                           (self.slow_start, "slow_start"),
                           (self.slow_stop, "slow_stop"),
                           (self.slow_step, "slow_step")):
            spin.setValue(float(sc[name]))
        self.fast_axis.setCurrentIndex(1)
        self.slow_axis.setCurrentIndex(0)
        if self.mode_combo.currentData() == "1d_tune":
            self.fast_step.setValue(50.0)

    def _on_busy_changed(self, busy: bool):
        self.start_btn.setEnabled(not busy)
        self.transfer_btn.setEnabled(not busy)
        self.abort_btn.setEnabled(busy)
        for _label, _spin, go, _grab in self.z_point_rows:
            go.setEnabled(not busy)

    def _mode_changed(self):
        """モードに応じて関係する入力行だけを有効化する。"""
        mode = self.mode_combo.currentData()
        pos_scan = mode in ("1d", "2d", "1d_tune")
        for w in (self.fast_axis, self.fast_start, self.fast_stop,
                  self.fast_step, self.z_mode_combo, self.rot_check):
            w.setEnabled(pos_scan)
        for w in (self.slow_axis, self.slow_start, self.slow_stop,
                  self.slow_step, self.snake_check):
            w.setEnabled(mode == "2d")
        for w in (self.tune_start, self.tune_stop, self.tune_step):
            w.setEnabled(mode in ("tune", "1d_tune"))
        # 1d_tune は各位置でエネルギースキャンするため位置 step は広めが
        # 標準 (50 µm)。既定値のままなら自動で切り替える (手入力は尊重)
        default_step = float(self.registry.current()["scan"]["fast_step"])
        if mode == "1d_tune" and self.fast_step.value() == default_step:
            self.fast_step.setValue(50.0)
        elif mode in ("1d", "2d") and self.fast_step.value() == 50.0:
            self.fast_step.setValue(default_step)
        # 実測 Z の入力欄: 1D は 2 行、2D は 4 行
        points_mode = pos_scan and self.z_mode_combo.currentData() == "points"
        self.z_points_widget.setVisible(points_mode)
        n_rows = 4 if mode == "2d" else 2
        for i, row in enumerate(self.z_point_rows):
            for w in row:
                w.setVisible(i < n_rows)
        self._update_estimate()

    def _collect_params(self) -> dict:
        mode = self.mode_combo.currentData()
        dzdx, dzdy, dydx, _src = self.registry.coefficients()
        n_pts = 4 if mode == "2d" else 2
        return {
            "mode": mode,
            "sensor_id": self.registry.current_id,
            "sensor_type": str(self.registry.current().get("sensor_type", "strip")),
            "fast_axis": self.fast_axis.currentData(),
            "fast_start": self.fast_start.value(),
            "fast_stop": self.fast_stop.value(),
            "fast_step": self.fast_step.value(),
            "slow_axis": self.slow_axis.currentData(),
            "slow_start": self.slow_start.value(),
            "slow_stop": self.slow_stop.value(),
            "slow_step": self.slow_step.value(),
            "snake": self.snake_check.isChecked(),
            # センサー傾き補正 (係数は現在のセンサーのフル較正 / 手動入力値)
            "z_mode": self.z_mode_combo.currentData(),
            "z_points": [row[1].value()
                         for row in self.z_point_rows[:n_pts]],
            "rot_correct": self.rot_check.isChecked(),
            "tilt_dzdx": dzdx, "tilt_dzdy": dzdy, "rot_dydx": dydx,
            "tune_start": self.tune_start.value(),
            "tune_stop": self.tune_stop.value(),
            "tune_step": self.tune_step.value(),
            "settle_s": self.settle_spin.value(),
            "point_timeout_s": self.timeout_spin.value(),
            "use_sequence": self.seq_check.isChecked(),
            "segments": self.segments_spin.value(),
            "avg_off": self.avg_off_check.isChecked(),
            "save_dir": self.save_dir_edit.text().strip(),
            "prefix": self.prefix_edit.text().strip() or "scan",
            "laser_on": self.laser_on_check.isChecked(),
            "laser_off": self.laser_off_check.isChecked(),
        }

    def _update_estimate(self):
        params = self._collect_params()
        self._update_z_point_labels(params)
        try:
            n = count_points(params)
        except Exception as exc:
            self.estimate_label.setText(f"条件エラー: {exc}")
            return
        if self.seq_check.isChecked():
            events = n * self.segments_spin.value()
            self.estimate_label.setText(
                f"測定点数: {n:,} 点 × {self.segments_spin.value():,} seg "
                f"= 約 {events:,} events")
        else:
            self.estimate_label.setText(f"測定点数: {n:,} 点")

    # ------------------------------------------------------------------
    # 実測 Z 方式 (始点・終点 / 四隅)
    # ------------------------------------------------------------------
    def _corners(self, params: dict) -> list[list[float]]:
        """実測 Z の測定位置 (論理座標)。現在位置が不明なら (0,0,0) 基準。"""
        base = self._get_stage_pos() or [0.0, 0.0, 0.0]
        try:
            return scan_corners(params, base)
        except Exception:
            return []

    def _update_z_point_labels(self, params: dict):
        if not hasattr(self, "z_point_rows"):
            return
        names = ["始点", "終点"] if params["mode"] != "2d" else \
            ["始点", "行の終点", "最終行の始点", "最終行の終点"]
        corners = self._corners(params)
        scanned = {params["fast_axis"]}
        if params["mode"] == "2d":
            scanned.add(params["slow_axis"])
        for i, (label, _spin, _go, _grab) in enumerate(self.z_point_rows):
            if i < len(corners) and i < len(names):
                coords = " ".join(f"{'XYZ'[ax]}={corners[i][ax]:.0f}"
                                  for ax in sorted(scanned) if ax < 2)
                label.setText(f"{names[i]} ({coords})")
            else:
                label.setText("")

    def _goto_z_point(self, index: int):
        if self.runner.is_busy() or not self.stage.connected:
            return
        cur = self._get_stage_pos()
        if cur is None:
            QMessageBox.warning(self, "移動できません",
                                "ステージ位置を取得できていません")
            return
        corners = self._corners(self._collect_params())
        if index >= len(corners):
            return
        x, y = corners[index][0], corners[index][1]

        def move():
            # Z は現在のまま (移動先でフォーカスを合わせて取り込む)。
            # 表示用のポーリング値は古い可能性があるので読み直す
            pos, _busy = self.stage.get_position_um()
            self.stage.move_to_um_relative(x, y, pos[2])
        self.caller.call(
            move,
            on_error=lambda exc: QMessageBox.warning(
                self, "ステージエラー", str(exc)))

    def _grab_z_point(self, index: int):
        if not self.stage.connected:
            QMessageBox.warning(self, "取り込めません",
                                "ステージが未接続です")
            return
        # 表示用のポーリング値ではなく、その場で読み直した Z を取り込む
        self.caller.call(
            self.stage.get_position_um,
            on_result=lambda res: self.z_point_rows[index][1].setValue(
                res[0][2]),
            on_error=lambda exc: QMessageBox.warning(
                self, "ステージエラー", str(exc)))

    # ------------------------------------------------------------------
    # データ回収 (オシロ → PC)
    # ------------------------------------------------------------------
    def _start_transfer(self):
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "転送開始不可",
                f"「{self.runner.current_name()}」の実行中です")
            return
        dest = self.transfer_dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "転送開始不可", "PC側保存先が空です")
            return
        date_str = self.transfer_date_edit.text().strip()
        if not (len(date_str) == 8 and date_str.isdigit()):
            QMessageBox.warning(self, "転送開始不可",
                                "日付は YYYYMMDD 形式で入力してください "
                                f"(入力値: {date_str!r})")
            return
        host = host_from_resource(self.scope.resource)
        user = getattr(self.cfg, "scope_smb_user", "<SCOPE_USER>")
        delete_after = self.transfer_delete_check.isChecked()
        year = date_str[:4]

        # パスワードは保存せず毎回入力 (セッション中は前回値をプリフィル)
        password, ok = QInputDialog.getText(
            self, "オシロ SSH パスワード",
            f"{user}@{host} のパスワード:",
            QLineEdit.Password, getattr(self, "_ssh_password", ""))
        if not ok:
            return
        if not password:
            QMessageBox.warning(self, "転送開始不可", "パスワードが空です")
            return
        self._ssh_password = password  # メモリ内のみ (設定には保存しない)

        lines = [
            f"回収元: {user}@{host}:"
            f"{self.cfg.scope_save_dir}\\{year}\\{date_str}",
            f"保存先: {Path(dest).resolve()}\\{year}\\{date_str}",
            "回収済みのファイル/フォルダは自動でスキップします。",
        ]
        if delete_after:
            lines.append("★ 全件のサイズ検証成功時のみ、オシロ側の"
                         "元ファイルを削除します ★")
        else:
            lines.append("オシロ側のファイルは残します (コピーのみ)。")
        lines.append("オシロのオートセーブが動作中でないことを確認して"
                     "ください。")
        ret = QMessageBox.question(
            self, "データ回収の確認", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        fetcher = ScpFetcher(
            host, user, password, self.cfg.scope_save_dir, dest)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = self._log_dir / f"transfer_{stamp}.csv"
        worker = TransferWorker(fetcher, date_str, delete_after, csv_path)
        self.runner.start("データ回収", worker, worker.finished_transfer,
                          self._finished_dialog("データ回収"))

    def _finished_dialog(self, title: str):
        """タスク終了時に結果ダイアログを出すコールバックを作る。"""
        def done(ok: bool, message: str, _worker):
            if ok:
                QMessageBox.information(self, f"{title}完了", message)
            else:
                QMessageBox.warning(self, f"{title}終了", message)
        return done

    # ------------------------------------------------------------------
    def _start_scan(self):
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "スキャン開始不可",
                f"「{self.runner.current_name()}」の実行中です")
            return
        # 保存先が起動時に自動で入れた日付フォルダのままなら、日付を
        # 跨いだときに今日のフォルダへ追従させる (前日フォルダへの混入防止)
        today_dir = self.cfg.dated_save_dir()
        if self.save_dir_edit.text().strip() == self._auto_save_dir \
                and today_dir != self._auto_save_dir:
            self.save_dir_edit.setText(today_dir)
        self._auto_save_dir = today_dir
        params = self._collect_params()

        # --- 事前条件チェック ---
        if not self.scope.connected:
            QMessageBox.warning(self, "スキャン開始不可",
                                "オシロスコープが未接続です")
            return
        mode = params["mode"]
        if mode in ("1d", "2d", "1d_tune") and not self.stage.connected:
            QMessageBox.warning(self, "スキャン開始不可",
                                "ステージが未接続です")
            return
        if mode in ("tune", "1d_tune") and not self.laser.connected:
            QMessageBox.warning(self, "スキャン開始不可",
                                "TUNE スキャンにはレーザー接続が必要です")
            return
        if (params["laser_on"] or params["laser_off"]) \
                and not self.laser.connected \
                and mode not in ("tune", "1d_tune"):
            QMessageBox.warning(self, "スキャン開始不可",
                                "レーザー自動 ON/OFF にはレーザー接続が必要です")
            return
        try:
            n = count_points(params)
        except Exception as exc:
            QMessageBox.warning(self, "条件エラー", str(exc))
            return

        # --- 保存先: ベースの下にスキャン毎のサブフォルダを自動作成 ---
        # 例: ...\data\2026\20260705\scan_20260705_143022_1dY
        # (フォルダ名からいつ・どのモードのデータか分かるようにする。
        #  PC 側 CSV も同じ日時スタンプなので対応付けできる)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if mode == "2d":
            tag = (f"2d{'XYZ'[params['fast_axis']]}"
                   f"{'XYZ'[params['slow_axis']]}")
        elif mode == "1d":
            tag = f"1d{'XYZ'[params['fast_axis']]}"
        elif mode == "1d_tune":
            tag = f"1d{'XYZ'[params['fast_axis']]}tune"
        else:
            tag = "tune"
        base_dir = params["save_dir"].rstrip("\\/")
        params["save_dir"] = f"{base_dir}\\scan_{stamp}_{tag}"

        # --- 確認ダイアログ (移動範囲と点数を明示して誤操作を防ぐ) ---
        lines = [f"センサー: {self.registry.current()['name']}",
                 f"測定点数: {n:,} 点"]
        if mode in ("1d", "2d", "1d_tune"):
            fa = "XYZ"[params["fast_axis"]]
            lines.append(
                f"高速軸 {fa}: {params['fast_start']:.1f} → "
                f"{params['fast_stop']:.1f} µm (step {params['fast_step']:.1f})")
        if mode == "2d":
            sa = "XYZ"[params["slow_axis"]]
            lines.append(
                f"低速軸 {sa}: {params['slow_start']:.1f} → "
                f"{params['slow_stop']:.1f} µm (step {params['slow_step']:.1f})")
        if mode in ("1d", "2d", "1d_tune"):
            # 補正適用後の点リストから実際の補正量の範囲を示す。
            # 開始時の現在位置を基準にする (スキャン本体と同じ計算)
            base = self._get_stage_pos() or [0.0, 0.0, 0.0]
            pts = build_points(params, base)
            z_mode = params["z_mode"] if 2 not in (
                params["fast_axis"],
                params["slow_axis"] if mode == "2d" else -1) else "off"
            zs = [pt["pos"][2] for pt in pts]
            z_desc = {key: label for label, key in Z_MODES}[z_mode]
            if z_mode == "off":
                lines.append("Z 追従: なし (Z は現在位置のまま)")
            else:
                lines.append(
                    f"Z 追従 [{z_desc}]: Z = {min(zs):+.1f} 〜 "
                    f"{max(zs):+.1f} µm (論理座標, 2 µm 刻みで移動)")
                if abs(zs[0] - base[2]) > 30.0:
                    lines.append(
                        f"★ 最初の点の Z ({zs[0]:+.1f}) が現在の Z "
                        f"({base[2]:+.1f}) から 30 µm 以上離れています。"
                        "原点・実測 Z が正しいか確認してください ★")
            if z_mode == "coef_origin":
                lines.append("※ 論理原点 (0,0,0) でフォーカスが合っている"
                             "ことが前提です (原点出し直後の状態)")
            elif z_mode == "coef_current":
                lines.append("※ 現在位置 (スキャン開始点) でフォーカスが"
                             "合っていることを確認してください")
            if params["rot_correct"]:
                plain = build_points({**params, "rot_correct": False}, base)
                shifts = [a["pos"][1] - b["pos"][1]
                          for a, b in zip(pts, plain)]
                lines.append(
                    f"回転追従: Y シフト {min(shifts):+.1f} 〜 "
                    f"{max(shifts):+.1f} µm")
            lines.append(f"傾き係数: {self.registry.summary()}")
        if mode in ("tune", "1d_tune"):
            prefix = "各位置で TUNE" if mode == "1d_tune" else "TUNE"
            lines.append(
                f"{prefix}: {params['tune_start']:.1f} → "
                f"{params['tune_stop']:.1f}% (step {params['tune_step']:.1f})")
            if mode == "1d_tune":
                lines.append("※ TUNE は manual モードに切り替わります。"
                             "周波数を変えた場合は事前に TUNE 最適化で"
                             "動作点を確認してください")
        if params["use_sequence"]:
            lines.append(f"シーケンス: {params['segments']:,} seg/点")
        if params["avg_off"]:
            lines.append("開始時に C1–C4 のアベレージングを OFF にします")
        lines.append(f"保存先 (オシロ側): {params['save_dir']}")
        if params["laser_on"]:
            lines.append("★ 開始時にレーザーを自動 ON します ★")
        if mode in ("1d", "2d", "1d_tune"):
            lines.append("座標は論理原点 (外枠エッジ=0,0,0) 基準・相対移動"
                         "です。原点出し (ステージパネル) 実施済みか確認して"
                         "ください。")
            lines.append("健全性確認: ビーム半径は数 µm のため、電極 (金属) "
                         "直上では信号がほぼ 0 になるはずです。最初の行の"
                         "データで 0 に落ちること (=フォーカス良好) を"
                         "確認してください。")
        lines.append("移動範囲内でステージが対物レンズ等と干渉しないことを"
                     "確認してください。")
        ret = QMessageBox.question(
            self, "スキャン開始の確認", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        # --- 実行 (CSV はオシロ側フォルダと同じ日時スタンプで対応付け) ---
        csv_path = self._log_dir / f"scan_{stamp}.csv"
        worker = ScanWorker(self.scope, self.stage, self.laser,
                            params, csv_path)
        self.runner.start("スキャン", worker, worker.finished_scan,
                          self._finished_dialog("スキャン"))

    # ------------------------------------------------------------------
    def shutdown(self):
        self.caller.shutdown()
