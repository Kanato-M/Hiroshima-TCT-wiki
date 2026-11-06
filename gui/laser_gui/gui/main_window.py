# -*- coding: utf-8 -*-
"""メインウィンドウ。

タブ構成:
  [制御] 3 カラムレイアウト (下記)
  [解析] 取得済みデータの簡易解析 (analysis\\run_analysis.py の GUI 実行)

レイアウト (16:9 横長モニター向け):
  ┌──────────────────────────────────────────────┐
  │ ツールバー: センサー選択 | 実行中タスクの進捗・中断 |      ■ 全停止 │
  ├──────────────────────────────────────────────┤
  │ [制御] [解析]                                                        │
  ├────────────────┬───────────────┬─────────────┤
  │ オシロ (ミラー+設定)       │ カメラ                   │ レーザー (+TUNE最適化)│
  │                            ├───────────────┼─────────────┤
  ├────────────────┤ ステージ                 │ 自動スキャン         │
  │ 通信ログ                   │ (操作/位置登録/          │ (スキャン条件/       │
  │                            │  アライメント)           │  データ回収)         │
  ├────────────────┴───────────────┴─────────────┤
  │ ステータスバー: 測定標準条件 (HV/LV, CH-パッド対応, センサー)        │
  └──────────────────────────────────────────────┘

「■ 全停止」はツールバーの右端に置く: 以前は左端にあり、すぐ下の
[制御]/[解析] タブを押そうとして誤クリックし、スキャンを止めてしまう
事故があった (2026-10)。タブは左端、全停止は右端で十分に離している。

暗室に入らずに: レーザー照射 (レーザーパネル) → 位置決め (カメラ+ステージ)
→ 波形確認・保存 (オシロパネル) が完結することを目的とする。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox,
                               QFrame, QLabel, QLayout, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QTabWidget, QToolBar,
                               QWidget)

from config import GuiConfig
from devices import FocusCamera, LecroyScope, PilasLaser, Shrc203Stage
from devices.sim_world import SimWorld
from sensors import SensorRegistry

from .analysis_panel import AnalysisPanel
from .camera_panel import CameraPanel
from .laser_panel import LaserPanel
from .log_panel import LogBus, LogPanel
from .scan_panel import ScanPanel
from .scope_panel import ScopePanel
from .sensor_dialog import SensorDialog
from .stage_panel import StagePanel
from .task_runner import TaskRunner, TaskStrip
from .widgets import ComboPopupFitter, NoWheelFilter


class MainWindow(QMainWindow):
    def __init__(self, cfg: GuiConfig):
        super().__init__()
        self.cfg = cfg
        title = "AC-LGAD レーザースキャン制御 GUI"
        if cfg.scope_simulate and cfg.laser_simulate:
            title += "  [シミュレーションモード]"
        self.setWindowTitle(title)
        # モニターの利用可能領域に合わせて起動する。
        # 高DPIスケーリング環境 (4K + 150〜250%) では論理解像度が
        # 1536x864 程度まで小さくなるため、余裕がない画面では最初から
        # 最大化して全域を使う (はみ出し・パネル潰れの防止)。
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(1760, avail.width() - 40)
            h = min(990, avail.height() - 80)
            self.resize(w, h)
            self.move(avail.x() + (avail.width() - w) // 2,
                      avail.y() + (avail.height() - h) // 2)
            if avail.width() < 1800 or avail.height() < 1000:
                self.setWindowState(Qt.WindowMaximized)
        else:
            self.resize(1600, 900)

        # スピンボックス / コンボボックス上のホイール操作で値が変わるのを
        # 止める (スクロール中の誤変更防止)。アプリ全体に 1 つだけ入れる
        self._wheel_filter = NoWheelFilter(self)
        QApplication.instance().installEventFilter(self._wheel_filter)

        # --- ログ集約 ---
        self.log_bus = LogBus(self)
        self.log_panel = LogPanel(cfg.log_dir)
        self.log_bus.message.connect(self.log_panel.append)

        # --- センサー登録辞書・タスクの排他実行 ---
        self.registry = SensorRegistry(cfg.log_dir)
        self.runner = TaskRunner(self)

        # --- デバイス生成 (接続はまだしない) ---
        self.scope = LecroyScope(
            cfg.scope_resource, timeout_ms=cfg.scope_timeout_ms,
            simulate=cfg.scope_simulate, log=self.log_bus.make_logger("SCOPE"))
        self.stage = Shrc203Stage(
            host=cfg.stage_host, port=cfg.stage_port,
            use_serial=cfg.stage_use_serial,
            serial_port=cfg.stage_serial_port, baudrate=cfg.stage_baudrate,
            um_per_pulse=cfg.stage_um_per_pulse,
            simulate=cfg.stage_simulate, log=self.log_bus.make_logger("STAGE"),
            frame_path=Path(cfg.log_dir) / "stage_frame.json")
        self.laser = PilasLaser(
            cfg.laser_port, baudrate=cfg.laser_baudrate,
            simulate=cfg.laser_simulate, log=self.log_bus.make_logger("LASER"))
        self.camera = FocusCamera(
            cfg.camera_index, simulate=cfg.camera_simulate,
            log=self.log_bus.make_logger("CAMERA"))
        # シミュレーション: ステージ位置・レーザー状態に応じた疑似振幅を
        # オシロへ接続する (アライメント等のシーケンスを最後まで試せる)
        if cfg.scope_simulate and cfg.stage_simulate and cfg.laser_simulate:
            self._sim_world = SimWorld(self.stage, self.laser, self.registry)
            self.scope.sim_amplitude = self._sim_world.amplitude

        # --- パネル生成 ---
        self.scope_panel = ScopePanel(self.scope, cfg, self.registry)
        self.camera_panel = CameraPanel(self.camera, cfg.capture_dir)
        self.stage_panel = StagePanel(
            self.stage, self.scope, self.laser, self.registry, self.runner,
            cfg)
        self.laser_panel = LaserPanel(
            self.laser, self.scope, self.registry, self.runner, cfg.log_dir,
            stage=self.stage, cfg=cfg)
        self.scan_panel = ScanPanel(
            self.scope, self.stage, self.laser, self.registry, self.runner,
            cfg, get_stage_pos=self.stage_panel.last_position)
        self.analysis_panel = AnalysisPanel(cfg)

        # スピンボックス/コンボは ±1e6 µm 等の表示幅で最小サイズが大きく、
        # 3カラム合計が論理解像度の幅を超えてしまう。推奨サイズはそのままに
        # 縮小だけ許可して、高DPI環境の狭い論理幅にも収まるようにする。
        # 併せて全レイアウトの余白・間隔を詰め、縦方向の必要量を減らす
        # (最大化時にパネルが潰れて重なる不具合の対策)。
        self._popup_fitter = ComboPopupFitter(self)
        for panel in (self.scope_panel, self.camera_panel, self.stage_panel,
                      self.laser_panel, self.scan_panel, self.log_panel,
                      self.analysis_panel):
            for w in panel.findChildren(QAbstractSpinBox):
                w.setMinimumWidth(min(64, w.minimumSizeHint().width()))
            # コンボは推奨幅 (項目が読める幅) 未満に縮めない。縮めると
            # 本体の文字が切れるだけでなく、Windows 11 スタイルでは
            # ドロップダウンの項目まで「...」になる (2026-10-07)。長い
            # 説明文の項目を持つコンボだけ 120 px で頭打ちにし、その
            # ドロップダウンは ComboPopupFitter で項目幅へ広げる
            for w in panel.findChildren(QComboBox):
                w.setMinimumWidth(min(120, w.minimumSizeHint().width()))
                w.installEventFilter(self._popup_fitter)
            for lay in panel.findChildren(QLayout):
                lay.setSpacing(4)
                if isinstance(lay.parent(), QWidget):
                    lay.setContentsMargins(6, 4, 6, 4)

        # --- レイアウト (16:9 横長 3カラム) ---
        # 左: オシロ (ミラー画面を大きく保つ) + 通信ログ
        # 中: カメラ (位置決め) + ステージ (操作) を縦に
        # 右: レーザー + 自動スキャンを縦に
        # 各カラムはスクロールエリアで包む: 画面がパネル群の最小サイズより
        # 小さい場合でもウィジェットが潰れて重ならず、スクロールバーが出る
        # (高DPIモニターで最大化したときの表示崩れ対策)。
        def scrollable(widget: QWidget) -> QScrollArea:
            area = QScrollArea()
            area.setWidget(widget)
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            return area

        mid_col = QSplitter(Qt.Vertical)
        mid_col.setChildrenCollapsible(False)
        mid_col.addWidget(self.camera_panel)
        mid_col.addWidget(self.stage_panel)
        mid_col.setSizes([330, 430])

        # 通信ログは左カラムの下へ (オシロのミラー画面は伸縮できるので
        # 余裕がある)。右カラムはレーザー + スキャンだけにして、通常の
        # 画面サイズで縦スクロールなしに全項目が見えるようにする
        left_col = QSplitter(Qt.Vertical)
        left_col.setChildrenCollapsible(False)
        left_col.addWidget(self.scope_panel)
        left_col.addWidget(self.log_panel)
        left_col.setSizes([700, 150])

        right_col = QSplitter(Qt.Vertical)
        right_col.setChildrenCollapsible(False)
        right_col.addWidget(self.laser_panel)
        right_col.addWidget(self.scan_panel)
        right_col.setSizes([300, 560])

        main_split = QSplitter(Qt.Horizontal)
        main_split.setChildrenCollapsible(False)
        main_split.addWidget(scrollable(left_col))
        main_split.addWidget(scrollable(mid_col))
        main_split.addWidget(scrollable(right_col))
        # 最小論理幅 1600 px (main.py) で 3 列とも各パネルの最小幅
        # (オシロ ~520 / ステージ ~500 / レーザー・スキャン ~490) が収まる配分
        main_split.setSizes([530, 510, 560])

        # タブ化: [制御] は 3 カラム、[解析] は解析パネル
        tabs = QTabWidget()
        tabs.addTab(main_split, "制御")
        tabs.addTab(self.analysis_panel, "解析")
        self.setCentralWidget(tabs)

        self._build_toolbar()
        self.registry.subscribe(self._refresh_sensor)
        self._refresh_sensor()

        self.log_bus.make_logger("GUI")("INFO", "GUI 起動")

    # ------------------------------------------------------------------
    def _build_toolbar(self):
        """ツールバー: [センサー選択] [タスク進捗・中断] ... [全停止]。"""
        toolbar = QToolBar("メイン")
        toolbar.setMovable(False)
        toolbar.setContextMenuPolicy(Qt.PreventContextMenu)  # 非表示化を防ぐ
        self.addToolBar(toolbar)
        toolbar.setStyleSheet(
            "QToolBar { background: #37474f; spacing: 6px; padding: 3px; }"
            "QToolBar QLabel { color: white; }")

        toolbar.addWidget(QLabel(" センサー: "))
        self.sensor_combo = QComboBox()
        self.sensor_combo.setMinimumWidth(220)
        self.sensor_combo.installEventFilter(self._popup_fitter)
        self.sensor_combo.setToolTip(
            "測定するセンサー / DUT。切り替えるとスキャン既定範囲・傾き係数・\n"
            "登録位置・読み出し対応がそのセンサーのものに入れ替わる")
        self.sensor_combo.activated.connect(self._sensor_selected)
        toolbar.addWidget(self.sensor_combo)
        edit_btn = QPushButton("編集...")
        edit_btn.setToolTip("センサーの形状・読み出し対応を編集 / 新規登録 / 削除")
        edit_btn.clicked.connect(self._edit_sensor)
        toolbar.addWidget(edit_btn)
        self.runner.busy_changed.connect(
            lambda busy: (self.sensor_combo.setEnabled(not busy),
                          edit_btn.setEnabled(not busy)))

        toolbar.addSeparator()
        self.task_strip = TaskStrip(self.runner)
        self.task_strip.setSizePolicy(QSizePolicy.Expanding,
                                      QSizePolicy.Preferred)
        toolbar.addWidget(self.task_strip)

        # 全停止は右端 (タブや他の操作ボタンから離す)。QPushButton にして
        # 大きめの押しやすい的にするが、周囲に他のボタンを置かない
        toolbar.addSeparator()
        estop = QPushButton("■ 全停止")
        estop.setToolTip("レーザー OFF / ステージ停止 / オシロ STOP / "
                         "実行中タスクの中断")
        estop.setStyleSheet(
            "QPushButton { color: white; background: #b71c1c;"
            " font-weight: bold; padding: 5px 18px; border-radius: 4px;"
            " margin-left: 24px; }"
            "QPushButton:pressed { background: #7f0000; }")
        estop.clicked.connect(self._emergency_stop_all)
        toolbar.addWidget(estop)

    def _refresh_sensor(self):
        """センサー選択コンボとステータスバーを登録辞書に合わせる。"""
        self.sensor_combo.blockSignals(True)
        self.sensor_combo.clear()
        for sid, name in self.registry.items():
            self.sensor_combo.addItem(name, sid)
        self.sensor_combo.setCurrentIndex(
            self.sensor_combo.findData(self.registry.current_id))
        self.sensor_combo.blockSignals(False)

        # ステータスバー: 測定標準条件の常時表示 (設定ミス防止のメモ)
        prof = self.registry.current()
        pads = "  ".join(
            f"{ch}={entry.get('pad') or '?'}"
            for ch, entry in prof["readout"].items() if entry.get("use", True))
        cfg = self.cfg
        self.statusBar().showMessage(
            f"センサー: {prof['name']} | "
            f"HV {cfg.hv_bias_v:g} V / LV {cfg.lv_bias_v:g} V | "
            f"読み出し {pads} | ピッチ {prof['pitch_um']:g} µm")

    def _sensor_selected(self, index: int):
        sid = self.sensor_combo.itemData(index)
        if sid is None or sid == self.registry.current_id:
            return
        name = self.sensor_combo.itemText(index)
        ret = QMessageBox.question(
            self, "センサーの切り替え",
            f"センサーを「{name}」に切り替えます。\n"
            "スキャン範囲は切り替え先の既定値に置き換わります。\n"
            "載せ替え後は原点出しをやり直してください。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret == QMessageBox.Yes:
            self.registry.select(sid)
        else:
            self._refresh_sensor()   # コンボの表示を元に戻す

    def _edit_sensor(self):
        SensorDialog(self.registry, self).exec()

    # ------------------------------------------------------------------
    def _emergency_stop_all(self):
        """非常停止: 接続中の全デバイスを安全側へ倒す。

        優先順位はスキャン中断 → レーザー停止 → ステージ停止 → オシロ取得停止。
        (各パネルの caller 経由なので互いにブロックしない)
        """
        self.runner.abort()
        self.laser_panel.emergency_stop()
        self.stage_panel.emergency_stop()
        self.scope_panel.emergency_stop()
        self.log_bus.make_logger("GUI")("INFO", "★ 全停止を実行しました ★")

    def closeEvent(self, event):
        # スキャン実行中の誤終了防止
        if self.runner.is_busy():
            ret = QMessageBox.question(
                self, "終了確認",
                f"「{self.runner.current_name()}」の実行中です。"
                "中断して終了しますか?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
        else:
            # 発振中の閉じ忘れ防止: レーザーONのまま終了しようとしたら警告
            ret = QMessageBox.question(
                self, "終了確認",
                "GUIを終了します。レーザーが発振中の場合は OFF にしてから\n"
                "終了することを推奨します。終了しますか?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
        self.runner.shutdown()
        for panel in (self.scan_panel, self.camera_panel, self.scope_panel,
                      self.stage_panel, self.laser_panel,
                      self.analysis_panel):
            try:
                panel.shutdown()
            except Exception:
                pass
        self.log_panel.close_file()
        event.accept()
