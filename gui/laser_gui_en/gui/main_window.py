# -*- coding: utf-8 -*-
"""Main window.

Tabs:
  [Control]  3-column layout (below)
  [Analysis] quick analysis of acquired data (runs
             analysis\\run_analysis.py from the GUI)

Layout (for a 16:9 landscape monitor):
  +--------------------------------------------------------------------+
  | Toolbar: sensor selector | task progress / Abort |      ■ STOP ALL |
  +--------------------------------------------------------------------+
  | [Control] [Analysis]                                               |
  +----------------------+----------------------+----------------------+
  | Scope                | Camera               | Laser                |
  | (mirror + settings)  |                      | (+ TUNE optimization)|
  |                      +----------------------+----------------------+
  +----------------------+ Stage                | Auto Scan            |
  | Communication Log    | (control / saved     | (scan conditions /   |
  |                      |  positions / align.) |  Data Retrieval)     |
  +----------------------+----------------------+----------------------+
  | Status bar: standard conditions (HV/LV, CH-pad mapping, sensor)    |
  +--------------------------------------------------------------------+

"■ STOP ALL" sits at the right end of the toolbar: it used to be at the
left end, and people aiming for the [Control]/[Analysis] tabs just below
clicked it by mistake and stopped a running scan (2026-10). The tabs are
at the left end and STOP ALL at the right end, far enough apart.

Goal: without entering the dark room, complete laser emission (laser
panel) → positioning (camera + stage) → waveform check and saving (scope
panel).
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
        title = "AC-LGAD Laser Scan Control GUI"
        if cfg.scope_simulate and cfg.laser_simulate:
            title += "  [SIMULATION MODE]"
        self.setWindowTitle(title)
        # Start with a size that fits the monitor's available area.
        # With high-DPI scaling (4K + 150-250%) the logical resolution
        # shrinks to about 1536x864, so on screens without headroom start
        # maximized and use the whole area (prevents overflow and
        # squashed panels).
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

        # Stop the wheel from changing values over spin boxes / combo
        # boxes (prevents accidental changes while scrolling). Only one
        # filter is installed for the whole application
        self._wheel_filter = NoWheelFilter(self)
        QApplication.instance().installEventFilter(self._wheel_filter)

        # --- log aggregation ---
        self.log_bus = LogBus(self)
        self.log_panel = LogPanel(cfg.log_dir)
        self.log_bus.message.connect(self.log_panel.append)

        # --- sensor registry / exclusive task execution ---
        self.registry = SensorRegistry(cfg.log_dir)
        self.runner = TaskRunner(self)

        # --- create the devices (not connected yet) ---
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
        # Simulation: feed the scope a pseudo amplitude that depends on
        # the stage position and the laser state (lets sequences such as
        # the alignment be tried all the way through)
        if cfg.scope_simulate and cfg.stage_simulate and cfg.laser_simulate:
            self._sim_world = SimWorld(self.stage, self.laser, self.registry)
            self.scope.sim_amplitude = self._sim_world.amplitude

        # --- create the panels ---
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

        # Spin boxes / combo boxes have a large minimum size because of
        # display widths such as ±1e6 µm, and the three columns together
        # exceed the logical screen width. Keep the size hints but allow
        # shrinking, so the GUI also fits the narrow logical widths of
        # high-DPI setups. In addition, tighten the margins and spacing
        # of every layout to reduce the vertical space needed (fix for
        # panels being squashed and overlapping when maximized).
        self._popup_fitter = ComboPopupFitter(self)
        for panel in (self.scope_panel, self.camera_panel, self.stage_panel,
                      self.laser_panel, self.scan_panel, self.log_panel,
                      self.analysis_panel):
            for w in panel.findChildren(QAbstractSpinBox):
                w.setMinimumWidth(min(64, w.minimumSizeHint().width()))
            # Combo boxes are not shrunk below their size hint (the width
            # at which the items are readable). Shrinking them not only
            # clips the text in the box; in the Windows 11 style even the
            # drop-down items turn into "..." (2026-10-07). Only combos
            # with long descriptive items are capped at 120 px, and their
            # drop-down is widened to the item width by ComboPopupFitter
            for w in panel.findChildren(QComboBox):
                w.setMinimumWidth(min(120, w.minimumSizeHint().width()))
                w.installEventFilter(self._popup_fitter)
            for lay in panel.findChildren(QLayout):
                lay.setSpacing(4)
                if isinstance(lay.parent(), QWidget):
                    lay.setContentsMargins(6, 4, 6, 4)

        # --- layout (16:9 landscape, 3 columns) ---
        # left:   scope (keeps the mirror image large) + communication log
        # middle: camera (positioning) + stage (control), stacked
        # right:  laser + auto scan, stacked
        # Each column is wrapped in a scroll area: even when the screen is
        # smaller than the minimum size of the panels, the widgets are not
        # squashed into each other and a scroll bar appears instead
        # (fix for the broken display when maximized on high-DPI monitors).
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

        # The communication log goes below the left column (the scope
        # mirror image can stretch, so there is room). The right column
        # holds only laser + scan, so that every item is visible without
        # vertical scrolling at normal screen sizes
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
        # At the minimum logical width of 1600 px (main.py) all three
        # columns fit the minimum widths of their panels (scope ~520 /
        # stage ~500 / laser + scan ~490)
        main_split.setSizes([530, 510, 560])

        # Tabs: [Control] is the 3 columns, [Analysis] the analysis panel
        tabs = QTabWidget()
        tabs.addTab(main_split, "Control")
        tabs.addTab(self.analysis_panel, "Analysis")
        self.setCentralWidget(tabs)

        self._build_toolbar()
        self.registry.subscribe(self._refresh_sensor)
        self._refresh_sensor()

        self.log_bus.make_logger("GUI")("INFO", "GUI started")

    # ------------------------------------------------------------------
    def _build_toolbar(self):
        """Toolbar: [sensor selector] [task progress/Abort] ... [STOP ALL]."""
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        # keeps the toolbar from being hidden via the context menu
        toolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(toolbar)
        toolbar.setStyleSheet(
            "QToolBar { background: #37474f; spacing: 6px; padding: 3px; }"
            "QToolBar QLabel { color: white; }")

        toolbar.addWidget(QLabel(" Sensor: "))
        self.sensor_combo = QComboBox()
        self.sensor_combo.setMinimumWidth(220)
        self.sensor_combo.installEventFilter(self._popup_fitter)
        self.sensor_combo.setToolTip(
            "Sensor / DUT to measure. Switching replaces the default scan\n"
            "range, tilt coefficients, saved positions and readout mapping\n"
            "with those of the selected sensor")
        self.sensor_combo.activated.connect(self._sensor_selected)
        toolbar.addWidget(self.sensor_combo)
        edit_btn = QPushButton("Edit...")
        edit_btn.setToolTip("Edit the sensor geometry / readout mapping, "
                            "register a new sensor, or delete one")
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

        # STOP ALL goes at the right end (away from the tabs and the other
        # buttons). It is a QPushButton to give a large, easy-to-hit
        # target, but no other button is placed around it
        toolbar.addSeparator()
        estop = QPushButton("■ STOP ALL")
        estop.setToolTip("Laser OFF / stage stop / scope STOP / "
                         "abort the running task")
        estop.setStyleSheet(
            "QPushButton { color: white; background: #b71c1c;"
            " font-weight: bold; padding: 5px 18px; border-radius: 4px;"
            " margin-left: 24px; }"
            "QPushButton:pressed { background: #7f0000; }")
        estop.clicked.connect(self._emergency_stop_all)
        toolbar.addWidget(estop)

    def _refresh_sensor(self):
        """Sync the sensor combo box and the status bar with the registry."""
        self.sensor_combo.blockSignals(True)
        self.sensor_combo.clear()
        for sid, name in self.registry.items():
            self.sensor_combo.addItem(name, sid)
        self.sensor_combo.setCurrentIndex(
            self.sensor_combo.findData(self.registry.current_id))
        self.sensor_combo.blockSignals(False)

        # Status bar: always shows the standard measurement conditions
        # (a reminder that prevents setup mistakes)
        prof = self.registry.current()
        pads = "  ".join(
            f"{ch}={entry.get('pad') or '?'}"
            for ch, entry in prof["readout"].items() if entry.get("use", True))
        cfg = self.cfg
        self.statusBar().showMessage(
            f"Sensor: {prof['name']} | "
            f"HV {cfg.hv_bias_v:g} V / LV {cfg.lv_bias_v:g} V | "
            f"Readout {pads} | Pitch {prof['pitch_um']:g} µm")

    def _sensor_selected(self, index: int):
        sid = self.sensor_combo.itemData(index)
        if sid is None or sid == self.registry.current_id:
            return
        name = self.sensor_combo.itemText(index)
        ret = QMessageBox.question(
            self, "Switch sensor",
            f"Switch the sensor to \"{name}\"?\n"
            "The scan range will be replaced by that sensor's defaults.\n"
            "After remounting the sensor, run Find Origin again.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret == QMessageBox.Yes:
            self.registry.select(sid)
        else:
            self._refresh_sensor()   # restore the combo box selection

    def _edit_sensor(self):
        SensorDialog(self.registry, self).exec()

    # ------------------------------------------------------------------
    def _emergency_stop_all(self):
        """Emergency stop: put every connected device into a safe state.

        Priority: abort the scan → stop the laser → stop the stage → stop
        the scope acquisition.
        (Each goes through its panel's caller, so they never block one
        another.)
        """
        self.runner.abort()
        self.laser_panel.emergency_stop()
        self.stage_panel.emergency_stop()
        self.scope_panel.emergency_stop()
        self.log_bus.make_logger("GUI")("INFO", "★ STOP ALL executed ★")

    def closeEvent(self, event):
        # Prevents quitting by mistake while a scan is running
        if self.runner.is_busy():
            ret = QMessageBox.question(
                self, "Confirm exit",
                f"\"{self.runner.current_name()}\" is running. "
                "Abort it and exit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
        else:
            # Guards against leaving the laser emitting: warn when the
            # GUI is closed, in case the laser is still ON
            ret = QMessageBox.question(
                self, "Confirm exit",
                "About to exit the GUI. If the laser is still emitting, it\n"
                "is recommended to turn it OFF first. Exit now?",
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
