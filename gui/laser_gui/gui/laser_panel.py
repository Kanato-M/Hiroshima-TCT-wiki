# -*- coding: utf-8 -*-
"""PiLAS DX レーザー制御パネル。

- 接続 / 状態表示 (mState? を周期ポーリング)
- トリガーソース・周波数・TUNE の設定
- 標準設定の一括適用 (internal / 1 MHz / TUNE manual 50 % = MIP 相当。
  config.py の laser_freq_hz, laser_tune_auto, laser_tune_percent)
- 発振 ON/OFF (誤操作防止のため ON は確認ダイアログ付き)
- TUNE 最適化 (目標振幅に最も近い TUNE を自動で探す。gui/tune_worker.py。
  方式: 単一 CH / 隣接 2ch 合計 A_lead+A_sub をストリップ中間で MIP 相当
  (DESY 2026: 64 ± 1 mV) に合わせる)
- 任意コマンド送信コンソール

TUNE と信号振幅の関係はカメラ用可視光照明の ON/OFF などの条件で変わる
ため、固定の「TUNE → 振幅」換算は表示しない。振幅を合わせたいときは
TUNE 最適化で実測に合わせる。
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QVBoxLayout)

import sensors
from .tune_worker import TuneOptimizeWorker
from .widgets import ElidedLabel
from .worker import AsyncCaller

# よく使う繰り返し周波数のプリセット [Hz]。標準は 1 MHz (2026-11-01 に
# TCT 推奨の 1.25 MHz から変更。config.py の laser_freq_hz)
FREQ_PRESETS = [
    ("1 kHz", 1_000), ("10 kHz", 10_000), ("100 kHz", 100_000),
    ("1 MHz (標準)", 1_000_000), ("1.25 MHz (TCT推奨)", 1_250_000),
    ("10 MHz", 10_000_000), ("40 MHz", 40_000_000),
]


class LaserPanel(QGroupBox):
    POLL_MS = 2000  # mState? ポーリング周期

    def __init__(self, laser, scope, registry, runner, log_dir: str,
                 stage=None, parent=None, cfg=None):
        super().__init__("レーザー (PiLAS DX)", parent)
        self.laser = laser
        self.scope = scope
        self.stage = stage      # TUNE 最適化のストリップ中間移動に使う
        # 標準条件 (config.py)。cfg 無しで生成された場合も同じ既定値
        self.std_freq_hz = int(getattr(cfg, "laser_freq_hz", 1_000_000))
        self.std_tune_auto = bool(getattr(cfg, "laser_tune_auto", False))
        self.std_tune_percent = float(
            getattr(cfg, "laser_tune_percent", 50.0))
        self.registry = registry
        self.runner = runner
        self._log_dir = Path(log_dir)
        self.caller = AsyncCaller("laser", self)
        self._synced = False   # 接続後、実機の設定値を入力欄へ反映済みか
        self._build_ui()
        registry.subscribe(self.refresh_sensor)
        runner.busy_changed.connect(
            lambda busy: self.tune_opt_btn.setEnabled(not busy))
        self.refresh_sensor()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_MS)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_pending = False

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- 接続行 ---
        conn_row = QHBoxLayout()
        self.port_edit = QLineEdit(self.laser.port)
        self.port_edit.setToolTip("COMポート (例: COM4, /dev/ttyUSB0)")
        self.connect_btn = QPushButton("接続")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.conn_label = QLabel("未接続")
        self.conn_label.setStyleSheet("color: gray;")
        conn_row.addWidget(QLabel("ポート:"))
        conn_row.addWidget(self.port_edit, 1)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.conn_label)
        layout.addLayout(conn_row)

        # --- 状態表示 ---
        status = QGridLayout()
        self.emission_label = QLabel("発振: ---")
        self.emission_label.setAlignment(Qt.AlignCenter)
        self.emission_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 2px;"
            "background: #444; color: white; border-radius: 4px;")
        status.addWidget(self.emission_label, 0, 0, 1, 5)
        # 状態文字列は長さが変わる (例: Interlock 開放の警告) ので、
        # パネル幅を押し広げない省略表示ラベルにする
        self.interlock_label = ElidedLabel("Interlock: ---")
        self.trig_label = ElidedLabel("Trig: ---")
        self.freq_label = ElidedLabel("Freq: ---")
        self.tune_label = ElidedLabel("Tune: ---")
        self.temp_label = ElidedLabel("Head: ---")
        self.temp_label.setToolTip("レーザーヘッド温度")
        status.addWidget(self.interlock_label, 1, 0)
        status.addWidget(self.trig_label, 1, 1)
        status.addWidget(self.freq_label, 1, 2)
        status.addWidget(self.tune_label, 1, 3)
        status.addWidget(self.temp_label, 1, 4)
        # 省略表示ラベルは自分では幅を要求しないので、列の伸縮比で幅を配る
        for col, stretch in enumerate((3, 3, 3, 3, 4)):
            status.setColumnStretch(col, stretch)
        layout.addLayout(status)

        # --- 設定 ---
        cfg = QGridLayout()
        cfg.addWidget(QLabel("トリガーソース:"), 0, 0)
        self.trig_combo = QComboBox()
        # (表示名, ts= の値)。TCT測定の標準は internal (レーザーが基準クロック)
        self.trig_combo.addItem("internal (ts=0)", 0)
        self.trig_combo.addItem("ext. TTL (ts=2)", 2)
        self.trig_combo.addItem("ext. Adj (ts=1)", 1)
        cfg.addWidget(self.trig_combo, 0, 1)
        apply_trig = QPushButton("設定")
        apply_trig.clicked.connect(self._apply_trigger)
        cfg.addWidget(apply_trig, 0, 2)

        cfg.addWidget(QLabel("繰り返し周波数:"), 1, 0)
        self.freq_combo = QComboBox()
        self.freq_combo.setEditable(True)  # 任意の Hz 値も直接入力可
        for label, hz in FREQ_PRESETS:
            self.freq_combo.addItem(label, hz)
        # 初期選択は標準値 (1 MHz)。接続後は実機の現在値に同期する
        self._select_freq(self.std_freq_hz)
        cfg.addWidget(self.freq_combo, 1, 1)
        apply_freq = QPushButton("設定")
        apply_freq.clicked.connect(self._apply_frequency)
        cfg.addWidget(apply_freq, 1, 2)

        cfg.addWidget(QLabel("TUNE:"), 2, 0)
        tune_row = QHBoxLayout()
        self.tune_mode_combo = QComboBox()
        self.tune_mode_combo.addItem("auto", True)
        self.tune_mode_combo.addItem("manual", False)
        # 標準は manual 50 % (MIP 相当)
        self.tune_mode_combo.setCurrentIndex(
            self.tune_mode_combo.findData(self.std_tune_auto))
        self.tune_spin = QDoubleSpinBox()
        self.tune_spin.setRange(0.0, 100.0)
        self.tune_spin.setDecimals(1)
        self.tune_spin.setValue(self.std_tune_percent)
        self.tune_spin.setSuffix(" %")
        self.tune_spin.setToolTip(
            "TUNE値 (0%=パルス振幅大, 100%=小)。最適値は繰り返し周波数に\n"
            f"依存する。標準は manual {self.std_tune_percent:g}% "
            f"({self.std_freq_hz / 1e6:g} MHz) = MIP 相当の出力。\n"
            "別の振幅に合わせるには下の TUNE 最適化を使う")
        tune_row.addWidget(self.tune_mode_combo)
        tune_row.addWidget(self.tune_spin)
        cfg.addLayout(tune_row, 2, 1)
        apply_tune = QPushButton("設定")
        apply_tune.clicked.connect(self._apply_tune)
        cfg.addWidget(apply_tune, 2, 2)

        # --- 標準設定の一括適用 (オシロパネルの同名ボタンと対) ---
        std_mode = "auto" if self.std_tune_auto else "manual"
        self.std_btn = QPushButton(
            f"★ 標準設定を適用 ({self.std_freq_hz / 1e6:g} MHz / "
            f"TUNE {std_mode} {self.std_tune_percent:g}% = MIP 相当) ★")
        self.std_btn.setToolTip(
            "レーザーの測定標準条件を一括送信: トリガー internal (ts=0), "
            f"繰り返し周波数 {self.std_freq_hz / 1e6:g} MHz, "
            f"TUNE {std_mode} {self.std_tune_percent:g}% "
            "(= MIP 相当の出力。隣接 2ch 合計をストリップ中間で 64 mV に\n"
            "合わせた値)。入力欄も標準値へ戻す。発振 ON/OFF は変えない")
        self.std_btn.setStyleSheet("font-weight: bold;")
        self.std_btn.clicked.connect(self._apply_standard_defaults)
        cfg.addWidget(self.std_btn, 3, 0, 1, 3)
        layout.addLayout(cfg)

        # --- TUNE 最適化 (波形振幅ベース) ---
        # TUNE を 100% (光が弱い側) から下げながらオシロの振幅を測り、
        # 目標振幅に達した TUNE に設定する。最適 TUNE は周波数依存なので、
        # 周波数を変えて信号が消えたときはまずこれを実行する
        # 方式 (2026-10-07 追加):
        #   single: 1 つの CH の振幅を目標に合わせる (従来)
        #   sum2  : 隣接 2 ストリップを読む CH の振幅合計 A_lead + A_sub を
        #           目標に合わせる。合計はビーム位置 (2 ストリップ間) に
        #           鈍感なので、ストリップ中間で MIP 相当 (DESY ビームテスト
        #           2026: 64 ± 1 mV) に合わせる用途
        opt_box = QGroupBox("TUNE 最適化 (目標振幅に合わせる)")
        opt_box.setToolTip(
            "レーザー ON・フォーカス済み・トリガー動作中に実行すること\n"
            "(オシロ接続が必要)。単一 CH はストリップ横のシリコン上、\n"
            "隣接 2ch 合計はストリップ中間 (自動移動可) で測る")
        opt_v = QVBoxLayout(opt_box)
        # 1 行に詰め込むと右カラムの最小幅が 620 px を超えて左カラム (オシロ)
        # を圧迫し、コンボ/スピンが 64 px に潰れて "..." 表示になる。
        # [方式, CH] / [目標, ±, step, 最適化] / [移動チェック] の 3 行に分ける
        row0 = QHBoxLayout()
        opt_v.addLayout(row0)
        opt = QHBoxLayout()
        row0.addWidget(QLabel("方式:"))
        self.tune_mode_sel = QComboBox()
        self.tune_mode_sel.addItem("単一 CH", "single")
        self.tune_mode_sel.addItem("隣接 2ch 合計 (MIP 相当)", "sum2")
        self.tune_mode_sel.setToolTip(
            "単一 CH: その CH の振幅 (PKPK) を目標に合わせる\n"
            "隣接 2ch 合計: 隣り合う 2 ストリップの CH の振幅合計\n"
            "  A_lead + A_sub を目標に合わせる。合計はビーム位置に鈍感\n"
            "  なのでストリップ中間 (ピッチ 500 µm なら 250 µm) で測る。\n"
            "  DESY ビームテストの MIP: 中間で 64 ± 1 mV (近傍 77 mV)")
        self.tune_mode_sel.currentIndexChanged.connect(
            self._on_tune_mode_changed)
        row0.addWidget(self.tune_mode_sel)
        self.tune_ch_label = QLabel("CH:")
        row0.addWidget(self.tune_ch_label)
        self.tune_ch = QComboBox()
        row0.addWidget(self.tune_ch)
        self.tune_pair = QComboBox()
        self.tune_pair.setToolTip("隣り合う電極を読む 2 つの CH の組")
        row0.addWidget(self.tune_pair)
        row0.addStretch(1)
        opt.addWidget(QLabel("目標"))
        self.tune_target_spin = QDoubleSpinBox()
        self.tune_target_spin.setRange(5.0, 2000.0)
        self.tune_target_spin.setValue(200.0)
        self.tune_target_spin.setSuffix(" mV")
        self.tune_target_spin.setDecimals(0)
        self.tune_target_spin.setToolTip(
            "この振幅 (PKPK, 2ch 合計方式では合計) に最も近い TUNE を"
            "探して設定する")
        opt.addWidget(self.tune_target_spin)
        opt.addWidget(QLabel("±"))
        self.tune_tol_spin = QDoubleSpinBox()
        self.tune_tol_spin.setRange(0.1, 50.0)
        self.tune_tol_spin.setValue(1.0)
        self.tune_tol_spin.setSuffix(" mV")
        self.tune_tol_spin.setDecimals(1)
        self.tune_tol_spin.setToolTip(
            "許容誤差。粗掃引で目標を跨いだ区間を精密掃引 (0.5% 刻み・"
            "多数回読み) し、この範囲に入った TUNE で止める")
        opt.addWidget(self.tune_tol_spin)
        opt.addWidget(QLabel("step"))
        self.tune_opt_step = QDoubleSpinBox()
        self.tune_opt_step.setRange(0.5, 20.0)
        self.tune_opt_step.setValue(2.0)
        self.tune_opt_step.setSuffix(" %")
        self.tune_opt_step.setDecimals(1)
        self.tune_opt_step.setToolTip("粗掃引の TUNE 刻み")
        opt.addWidget(self.tune_opt_step)
        opt.addStretch(1)
        self.tune_opt_btn = QPushButton("最適化")
        self.tune_opt_btn.clicked.connect(self._start_tune_optimize)
        opt.addWidget(self.tune_opt_btn)
        opt_v.addLayout(opt)
        self.tune_move_chk = QCheckBox(
            "2 ストリップの中間 Y へ移動して測定 (現在の X, 終了後に戻る)")
        self.tune_move_chk.setChecked(True)
        self.tune_move_chk.setToolTip(
            "選んだ 2 CH の電極中心の中間 Y へ移動してから測定する\n"
            "(面内回転・チルト係数で Y/Z を補正。ステージ接続が必要)。\n"
            "完了・中断後は元の位置へ戻る。OFF なら現在位置で測る")
        opt_v.addWidget(self.tune_move_chk)
        layout.addWidget(opt_box)
        # 方式ごとに目標値の既定を持つ (切替時に前回値を復元)
        self._tune_targets = {"single": 200.0, "sum2": 64.0}
        self._tune_mode_cur = None
        self._on_tune_mode_changed()

        # --- 発振 ON/OFF ---
        onoff_row = QHBoxLayout()
        self.on_btn = QPushButton("発振 ON (ld=1)")
        self.on_btn.setStyleSheet(
            "background: #b71c1c; color: white; font-weight: bold; padding: 8px;")
        self.on_btn.clicked.connect(self._emission_on)
        self.off_btn = QPushButton("発振 OFF (ld=0)")
        self.off_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        self.off_btn.clicked.connect(lambda: self._set_emission(False))
        onoff_row.addWidget(self.on_btn)
        onoff_row.addWidget(self.off_btn)
        layout.addLayout(onoff_row)

        # --- 任意コマンドコンソール ---
        console_row = QHBoxLayout()
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("任意コマンド (例: version?, mState?)")
        self.cmd_edit.returnPressed.connect(self._send_manual)
        send_btn = QPushButton("送信")
        send_btn.clicked.connect(self._send_manual)
        console_row.addWidget(self.cmd_edit, 1)
        console_row.addWidget(send_btn)
        layout.addLayout(console_row)
        layout.addStretch(1)

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool):
        for w in (self.on_btn, self.off_btn, self.cmd_edit,
                  self.trig_combo, self.freq_combo,
                  self.tune_mode_combo, self.tune_spin, self.std_btn):
            w.setEnabled(enabled)

    def _select_freq(self, hz: int):
        """周波数欄にプリセット (無ければ数値) を表示する。"""
        idx = self.freq_combo.findData(int(hz))
        if idx >= 0:
            self.freq_combo.setCurrentIndex(idx)
        else:
            self.freq_combo.setEditText(str(int(hz)))

    # ------------------------------------------------------------------
    # 接続
    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.laser.connected:
            self._poll_timer.stop()
            self.caller.call(self.laser.disconnect,
                             on_result=lambda _: self._on_disconnected())
        else:
            self.laser.port = self.port_edit.text().strip()
            self.connect_btn.setEnabled(False)
            self.caller.call(self.laser.connect,
                             on_result=lambda _: self._on_connected(),
                             on_error=self._on_conn_error)

    def _on_connected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("切断")
        self.conn_label.setText("接続中")
        self.conn_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        self._set_controls_enabled(True)
        self._synced = False
        self._poll_timer.start()
        self._poll_status()

    def _on_disconnected(self):
        self.connect_btn.setText("接続")
        self.conn_label.setText("未接続")
        self.conn_label.setStyleSheet("color: gray;")
        self._set_controls_enabled(False)
        self.emission_label.setText("発振: ---")
        self.emission_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 2px;"
            "background: #444; color: white; border-radius: 4px;")

    def _on_conn_error(self, exc: Exception):
        self.connect_btn.setEnabled(True)
        QMessageBox.warning(self, "レーザー接続エラー", str(exc))

    # ------------------------------------------------------------------
    # 状態ポーリング
    # ------------------------------------------------------------------
    def _poll_status(self):
        if self._poll_pending or not self.laser.connected:
            return
        self._poll_pending = True
        self.caller.call(self.laser.get_status,
                         on_result=self._update_status,
                         on_error=self._poll_failed)

    def _poll_failed(self, exc: Exception):
        self._poll_pending = False

    def _sync_inputs(self, st: dict):
        """接続直後に 1 回、実機の現在設定を入力欄へ反映する。

        入力欄が既定値のままだと、実機と違う値を気付かずに「設定」して
        しまう (特に周波数と TUNE)。
        """
        idx = self.trig_combo.findData(st["trigger_source"])
        if idx >= 0:
            self.trig_combo.setCurrentIndex(idx)
        self._select_freq(int(st["int_freq_hz"]))
        self.tune_mode_combo.setCurrentIndex(
            self.tune_mode_combo.findData(bool(st["tune_mode"])))
        self.tune_spin.setValue(st["tune_percent"])

    def _update_status(self, st: dict):
        self._poll_pending = False
        if not self._synced:
            self._synced = True
            self._sync_inputs(st)
        emitting = bool(st["emission_pulsed"])
        if emitting:
            self.emission_label.setText("発振: ON")
            self.emission_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; padding: 2px;"
                "background: #b71c1c; color: white; border-radius: 4px;")
        else:
            self.emission_label.setText("発振: OFF")
            self.emission_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; padding: 2px;"
                "background: #2e7d32; color: white; border-radius: 4px;")
        ilk = "有効" if st["interlock"] else "★開放 (発振不可)★"
        self.interlock_label.setText(f"Interlock: {ilk}")
        self.trig_label.setText(f"Trig: {st['trigger_source_name']}")
        if st["trigger_source"] == 0:
            self.freq_label.setText(f"Freq: {st['int_freq_hz'] / 1e6:.4g} MHz")
        else:
            self.freq_label.setText(f"Freq(ext): {st['ext_freq_hz'] / 1e6:.4g} MHz")
        mode = "auto" if st["tune_mode"] else "man"
        # mState の TUNE は 0.01% 単位 (旧版は 1/10 にしていて 650% 等と
        # 表示されていた。2026-10 修正)
        self.tune_label.setText(f"Tune: {mode} {st['tune_percent']:.1f}%")
        temp_ok = "OK" if st["temp_ok"] else "NG!"
        self.temp_label.setText(f"Head: {st['head_temp_c']:.2f} ℃ ({temp_ok})")

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _emission_on(self):
        # クラス1レーザーだが、装置状態の確認を促すため一段確認を挟む
        ret = QMessageBox.question(
            self, "発振開始の確認",
            "レーザー発振を開始します。\n"
            "インターロック・光学系のセットアップを確認しましたか?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._set_emission(True)

    def _set_emission(self, on: bool):
        self.caller.call(lambda: self.laser.set_emission(on),
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_trigger(self):
        source = self.trig_combo.currentData()
        self.caller.call(lambda: self.laser.set_trigger_source(source),
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_frequency(self):
        # プリセットと一致すればその Hz 値、そうでなければ入力テキストを
        # Hz 単位の数値として解釈する
        text = self.freq_combo.currentText()
        idx = self.freq_combo.findText(text)
        if idx >= 0:
            hz = self.freq_combo.itemData(idx)
        else:
            try:
                hz = int(float(text.replace(",", "").split()[0]))
            except (ValueError, IndexError):
                QMessageBox.warning(self, "入力エラー",
                                    "周波数は Hz 単位の数値で入力してください")
                return
        self.caller.call(lambda: self.laser.set_frequency(hz),
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_tune(self):
        auto = self.tune_mode_combo.currentData()
        percent = self.tune_spin.value()

        def apply():
            self.laser.set_tune_mode(auto)
            if not auto:
                self.laser.set_tune_percent(percent)
        self.caller.call(apply,
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_standard_defaults(self):
        """レーザーの測定標準条件 (config の laser_* 一式) を一括送信する。

        トリガー internal / 繰り返し周波数 1 MHz / TUNE manual 50 %
        (= MIP 相当)。UI の入力欄も標準値へ戻し、画面と実機のずれを防ぐ。
        発振 ON/OFF は変えない。
        """
        hz = self.std_freq_hz
        auto = self.std_tune_auto
        percent = self.std_tune_percent
        self.trig_combo.setCurrentIndex(self.trig_combo.findData(0))
        self._select_freq(hz)
        self.tune_mode_combo.setCurrentIndex(
            self.tune_mode_combo.findData(auto))
        self.tune_spin.setValue(percent)

        def apply():
            self.laser.set_trigger_source(0)
            self.laser.set_frequency(hz)
            self.laser.set_tune_mode(auto)
            if not auto:
                self.laser.set_tune_percent(percent)
        self.caller.call(apply,
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    # ------------------------------------------------------------------
    # TUNE 最適化 (波形振幅ベース)
    # ------------------------------------------------------------------
    def refresh_sensor(self):
        """読み出し CH / 隣接 CH 組の選択肢を現在のセンサーに合わせる。"""
        prof = self.registry.current()
        keep = self.tune_ch.currentData()
        self.tune_ch.clear()
        for ch in sensors.used_channels(prof):
            self.tune_ch.addItem(ch, ch)
        self.tune_ch.setCurrentIndex(max(self.tune_ch.findData(keep), 0))
        keep = self.tune_pair.currentData()
        self.tune_pair.clear()
        ro = prof["readout"]
        for a, b in self.registry.adjacent_pairs():
            self.tune_pair.addItem(
                f"{a}+{b} ({ro[a]['pad']}/{ro[b]['pad']})", (a, b))
        self.tune_pair.setCurrentIndex(
            max(self.tune_pair.findData(keep), 0))

    def _on_tune_mode_changed(self):
        """方式に応じて CH / CH 組・移動チェックの表示と目標既定値を切替。"""
        mode = self.tune_mode_sel.currentData()
        prev = self._tune_mode_cur
        if prev != mode:
            # 前の方式の目標値を控え、この方式の前回値 (初回は既定) を出す
            if prev is not None:
                self._tune_targets[prev] = self.tune_target_spin.value()
            self.tune_target_spin.setValue(self._tune_targets[mode])
            self._tune_mode_cur = mode
        single = mode == "single"
        self.tune_ch_label.setText("CH:" if single else "CH 組:")
        self.tune_ch.setVisible(single)
        self.tune_pair.setVisible(not single)
        self.tune_move_chk.setVisible(not single)

    def _start_tune_optimize(self):
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "実行不可",
                f"「{self.runner.current_name()}」の実行中です")
            return
        if not (self.scope.connected and self.laser.connected):
            QMessageBox.warning(self, "実行不可",
                                "オシロとレーザーの接続が必要です")
            return
        mode = self.tune_mode_sel.currentData()
        target = self.tune_target_spin.value()
        tol = self.tune_tol_spin.value()
        move_to = return_to = None
        if mode == "single":
            channels = [self.tune_ch.currentData()]
            what = f"{channels[0]} の振幅"
            where = "ビームが信号の見える位置 (ストリップ横のシリコン上)"
        else:
            pair = self.tune_pair.currentData()
            if pair is None:
                QMessageBox.warning(
                    self, "実行不可",
                    "このセンサーには隣り合う読み出し CH の組がありません "
                    "(センサー編集で読み出し CH の電極 Y を確認してください)")
                return
            channels = list(pair)
            what = f"{channels[0]}+{channels[1]} の振幅合計 (A_lead + A_sub)"
            if self.tune_move_chk.isChecked():
                if self.stage is None or not self.stage.connected:
                    QMessageBox.warning(
                        self, "実行不可",
                        "ストリップ中間への移動にはステージ接続が必要です "
                        "(移動せず現在位置で測るにはチェックを外す)")
                    return
                try:
                    cur, busy = self.stage.get_position_um()
                except Exception as exc:
                    QMessageBox.warning(self, "実行不可",
                                        f"ステージ位置を読めません: {exc}")
                    return
                if busy:
                    QMessageBox.warning(self, "実行不可", "ステージ移動中です")
                    return
                return_to = [float(v) for v in cur]
                move_to = self.registry.midpoint_position(
                    channels[0], channels[1], cur)
                what_mid = ("2 ピクセルの中間" if sensors.is_pixel(self.registry.current())
                            else "ストリップ中間")
                where = (f"{what_mid} X={move_to[0]:.0f}, Y={move_to[1]:.0f} µm "
                         f"(Z={move_to[2]:.0f}) へ移動して"
                         "測定し、終了後に現在位置へ戻る。"
                         "現在位置で合焦していること")
            else:
                where = ("ビームが 2 ピクセルの中間 (現在位置で測定)"
                         if sensors.is_pixel(self.registry.current())
                         else "ビームが 2 ストリップの中間 (現在位置で測定)")
        ret = QMessageBox.question(
            self, "TUNE 最適化",
            f"現在の周波数のまま TUNE を 100% 側から下げながら {what} を"
            f"測り、{target:.0f} ± {tol:.1f} mV に最も近い TUNE に設定します "
            "(粗掃引 → 精密掃引 → 検証)。\n"
            f"確認: (1) レーザー ON (2) {where}・フォーカス済 "
            "(3) トリガー動作中\n"
            "TUNE は manual モードに、オシロはシーケンス OFF・NORM に"
            "切り替わります。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {"channels": channels, "tune_start": 100.0,
                  "tune_stop": 0.0, "tune_step": self.tune_opt_step.value(),
                  "fine_step": 0.5, "reads": 3, "fine_reads": 8,
                  "target_mv": target, "tol_mv": tol,
                  "move_to": move_to, "return_to": return_to}
        worker = TuneOptimizeWorker(
            self.scope, self.laser, params,
            self._log_dir / f"tuneopt_{stamp}.csv", stage=self.stage)
        self.runner.start("TUNE 最適化", worker, worker.finished_tune,
                          self._on_tuneopt_finished)

    def _on_tuneopt_finished(self, ok: bool, message: str, _worker):
        # 最適化で TUNE が manual の新しい値になったので入力欄も合わせる
        self._synced = False
        self._poll_status()
        if ok:
            QMessageBox.information(self, "TUNE 最適化完了", message)
        else:
            QMessageBox.warning(self, "TUNE 最適化終了", message)

    def _send_manual(self):
        cmd = self.cmd_edit.text().strip()
        if not cmd:
            return
        self.cmd_edit.clear()
        # 応答はログパネルに流れるので結果コールバックは不要
        self.caller.call(lambda: self.laser.send(cmd),
                         on_error=self._show_error)

    def _show_error(self, exc: Exception):
        QMessageBox.warning(self, "レーザーエラー", str(exc))

    # ------------------------------------------------------------------
    def emergency_stop(self):
        """メインウィンドウの非常停止から呼ばれる。"""
        if self.laser.connected:
            self.caller.call(self.laser.emergency_off)

    def shutdown(self):
        self._poll_timer.stop()
        if self.laser.connected:
            try:
                self.laser.disconnect()
            except Exception:
                pass
        self.caller.shutdown()
