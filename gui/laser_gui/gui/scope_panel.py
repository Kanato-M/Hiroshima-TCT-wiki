# -*- coding: utf-8 -*-
"""LeCroy WavePro オシロスコープ制御パネル。

- 接続 / 画面ミラーリング (SCDP による周期スクリーンダンプ)
- トリガー設定 (EXT トリガー = レーザー TRIG OUT 同期が標準)
- チャンネル設定 (DC50Ω 結合が AC-LGAD 読み出しの標準)
- タイムベース / シーケンスモード / スイープクリア
- 波形保存設定 (保存先・prefix・オートセーブ) と本体設定の SAV/RCL
- 任意リモートコマンドコンソール
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox,
                               QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QSizePolicy, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from .worker import AsyncCaller, ScreenMirror

TDIV_PRESETS = ["200PS", "500PS", "1NS", "2NS", "5NS", "10NS", "20NS",
                "50NS", "100NS", "200NS", "500NS", "1US", "2US", "5US",
                "10US", "20US", "50US", "100US", "1MS"]
VDIV_PRESETS = ["1MV", "2MV", "5MV", "10MV", "20MV", "50MV",
                "100MV", "200MV", "500MV", "1V"]
COUPLINGS = ["D50", "D1M", "A1M", "GND"]
TRIG_COUPLINGS = ["DC", "AC", "HFREJ", "LFREJ"]


class ScopePanel(QGroupBox):
    def __init__(self, scope, cfg, registry, parent=None):
        super().__init__("オシロスコープ (LeCroy WavePro)", parent)
        self.scope = scope
        self.cfg = cfg
        self.registry = registry
        self.caller = AsyncCaller("scope", self)
        self._mirror: ScreenMirror | None = None
        # 手動保存の既定: <base>\<年>\<YYYYMMDD>\manual (日付ごとに整理)
        self._save_dir_default = cfg.dated_save_dir() + "\\manual"
        self._build_ui()
        registry.subscribe(self.refresh_sensor)
        self.refresh_sensor()

    def refresh_sensor(self):
        """チャンネル表のパッド名を現在のセンサーの読み出し対応に合わせる。"""
        readout = self.registry.current()["readout"]
        for ch, label in self.ch_labels.items():
            entry = readout.get(ch, {})
            pad = entry.get("pad", "")
            text = f"{ch} ({pad})" if pad else ch
            if not entry.get("use", True):
                text += " 未使用"
            label.setText(text)

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- 接続行 ---
        conn_row = QHBoxLayout()
        self.addr_edit = QLineEdit(self.scope.resource)
        self.addr_edit.setToolTip(
            "リソース名。\n"
            "VICP::<SCOPE_IP>::INSTR — LeCroy 標準 (Remote=TCPIP(VICP))\n"
            "TCPIP0::<SCOPE_IP>::inst0::INSTR — LXI(VXI-11) の場合\n"
            "接続時にもう一方の方式へ自動フォールバックする")
        self.connect_btn = QPushButton("接続")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.conn_label = QLabel("未接続")
        self.conn_label.setStyleSheet("color: gray;")
        conn_row.addWidget(QLabel("VISA:"))
        conn_row.addWidget(self.addr_edit, 1)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.conn_label)
        layout.addLayout(conn_row)

        # --- 画面ミラー ---
        self.screen_label = QLabel("画面ミラー停止中")
        # 最小高は低めにする (狭い論理解像度対策。余りはストレッチで拡大)
        self.screen_label.setMinimumHeight(140)
        # サイズポリシー Ignored: 受信したミラー画像 (pixmap) のサイズが
        # レイアウトの要求サイズに反映されないようにする。これが無いと
        # 「画像を受信 → ラベルの推奨サイズ拡大 → パネル全体が拡大 →
        # さらに大きい画像を生成」のループでパネルが際限なく育ち、
        # 下の設定タブまで押し広げられてしまう。
        self.screen_label.setSizePolicy(QSizePolicy.Ignored,
                                        QSizePolicy.Ignored)
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setStyleSheet(
            "background: black; color: gray; border: 1px solid #555;")
        layout.addWidget(self.screen_label, 1)

        mirror_row = QHBoxLayout()
        self.mirror_btn = QPushButton("ミラー開始")
        self.mirror_btn.setEnabled(False)
        self.mirror_btn.clicked.connect(self._toggle_mirror)
        mirror_row.addWidget(self.mirror_btn)
        mirror_row.addWidget(QLabel("更新間隔:"))
        self.mirror_interval = QComboBox()
        for label, sec in (("0.5 s", 0.5), ("1 s", 1.0), ("2 s", 2.0)):
            self.mirror_interval.addItem(label, sec)
        self.mirror_interval.setCurrentIndex(1)
        self.mirror_interval.currentIndexChanged.connect(
            self._mirror_interval_changed)
        mirror_row.addWidget(self.mirror_interval)
        mirror_row.addStretch(1)
        layout.addLayout(mirror_row)

        # --- 設定タブ ---
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_trigger_tab(), "トリガー/掃引")
        self.tabs.addTab(self._build_channel_tab(), "チャンネル")
        self.tabs.addTab(self._build_save_tab(), "保存")
        self.tabs.addTab(self._build_console_tab(), "コマンド")
        self.tabs.setEnabled(False)
        # 下部の操作パネルは高さ固定: ミラー画像の大きさに引きずられて
        # 拡大しないようにする (ミラーは上のラベルの余り領域だけで伸縮)
        self.tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.tabs)

    def _build_trigger_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)

        grid.addWidget(QLabel("トリガーモード:"), 0, 0)
        mode_row = QHBoxLayout()
        for mode in ("AUTO", "NORM", "SINGLE", "STOP"):
            btn = QPushButton(mode)
            btn.clicked.connect(
                lambda _=False, m=mode: self._call(
                    lambda: self.scope.set_trigger_mode(m)))
            mode_row.addWidget(btn)
        grid.addLayout(mode_row, 0, 1, 1, 3)

        grid.addWidget(QLabel("ソース:"), 1, 0)
        self.trig_src = QComboBox()
        self.trig_src.addItems(list(self.scope.TRIGGER_SOURCES))
        self.trig_src.setCurrentText(self.cfg.scope_trig_source)  # 標準は EX
        grid.addWidget(self.trig_src, 1, 1)
        grid.addWidget(QLabel("レベル [mV]:"), 1, 2)
        self.trig_level = QSpinBox()
        self.trig_level.setRange(-5000, 5000)
        self.trig_level.setSingleStep(10)
        self.trig_level.setValue(self.cfg.scope_trig_level_mv)  # 標準 410 mV
        grid.addWidget(self.trig_level, 1, 3)
        grid.addWidget(QLabel("スロープ:"), 2, 0)
        self.trig_slope = QComboBox()
        self.trig_slope.addItems(["POS", "NEG"])
        self.trig_slope.setCurrentText(self.cfg.scope_trig_slope)
        grid.addWidget(self.trig_slope, 2, 1)
        grid.addWidget(QLabel("結合:"), 2, 2)
        self.trig_coupling = QComboBox()
        self.trig_coupling.addItems(TRIG_COUPLINGS)
        self.trig_coupling.setCurrentText(self.cfg.scope_trig_coupling)
        grid.addWidget(self.trig_coupling, 2, 3)
        apply_btn = QPushButton("エッジトリガー適用")
        apply_btn.clicked.connect(self._apply_trigger)
        grid.addWidget(apply_btn, 3, 2, 1, 2)

        grid.addWidget(QLabel("タイムベース:"), 3, 0)
        self.tdiv_combo = QComboBox()
        self.tdiv_combo.addItems(TDIV_PRESETS)
        self.tdiv_combo.setCurrentText(self.cfg.scope_tdiv)  # 標準 1 ns/div
        grid.addWidget(self.tdiv_combo, 3, 1)

        grid.addWidget(QLabel("水平オフセット:"), 4, 0)
        self.trdl_spin = QDoubleSpinBox()
        self.trdl_spin.setRange(-1e6, 1e6)
        self.trdl_spin.setDecimals(2)
        self.trdl_spin.setSuffix(" ns")
        # 標準 -53 ns: 1 ns/div で表示範囲が +48〜+58 ns になる
        # (信号はレーザートリガーの約 52 ns 後。config.py 参照)
        self.trdl_spin.setValue(
            float(self.cfg.scope_trig_delay.upper().replace("NS", "")))
        grid.addWidget(self.trdl_spin, 4, 1)
        hax_btn = QPushButton("横軸適用")
        hax_btn.setToolTip("タイムベースと水平オフセット (TRDL) を送信")
        hax_btn.clicked.connect(self._apply_horizontal)
        grid.addWidget(hax_btn, 4, 2, 1, 2)

        self.seq_check = QCheckBox("シーケンスモード")
        self.seq_check.setToolTip("1トリガー=1セグメントで高速連続取得")
        grid.addWidget(self.seq_check, 5, 0)
        self.seq_segments = QSpinBox()
        self.seq_segments.setRange(2, 100000)
        self.seq_segments.setValue(200)
        self.seq_segments.setToolTip("セグメント数")
        grid.addWidget(self.seq_segments, 5, 1)
        seq_btn = QPushButton("適用")
        seq_btn.clicked.connect(self._apply_sequence)
        grid.addWidget(seq_btn, 5, 2)
        clsw_btn = QPushButton("スイープクリア")
        clsw_btn.setToolTip("アベレージングの積算をリセット (CLSW)")
        clsw_btn.clicked.connect(
            lambda: self._call(self.scope.clear_sweeps))
        grid.addWidget(clsw_btn, 5, 3)

        defaults_btn = QPushButton("★ 標準設定を一括適用 ★")
        defaults_btn.setToolTip(
            f"測定標準条件を送信: Trigger {self.cfg.scope_trig_source}/"
            f"{self.cfg.scope_trig_coupling}/{self.cfg.scope_trig_slope}/"
            f"{self.cfg.scope_trig_level_mv} mV, "
            f"{self.cfg.scope_tdiv.replace('NS', ' ns')}/div, "
            f"水平 {self.cfg.scope_trig_delay.replace('NS', ' ns')}, "
            f"C1–C4 {self.cfg.scope_vdiv.replace('MV', ' mV')}/div, "
            f"offset {self.cfg.scope_ch_offset_mv:+d} mV, DC50Ω, "
            "Averaging OFF")
        defaults_btn.setStyleSheet("font-weight: bold;")
        defaults_btn.clicked.connect(self._apply_standard_defaults)
        grid.addWidget(defaults_btn, 6, 0, 1, 4)
        grid.setRowStretch(7, 1)
        return w

    def _build_channel_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.addWidget(QLabel("CH"), 0, 0)
        grid.addWidget(QLabel("表示"), 0, 1)
        grid.addWidget(QLabel("VDIV"), 0, 2)
        # 単位は見出し側に置く: スピンにサフィックスを付けると Windows 11
        # スタイルの横並び矢印と合わせて幅が足りず数字が切れる
        grid.addWidget(QLabel("オフセット [mV]"), 0, 3)
        grid.addWidget(QLabel("結合"), 0, 4)
        avg_head = QLabel("平均")
        avg_head.setToolTip(
            "プリプロセッシング Averaging (app.Acquisition.Cx.AverageSweeps)。\n"
            "ON でスイープ数分を平均、OFF で平均なし (=1)")
        grid.addWidget(avg_head, 0, 5)
        grid.addWidget(QLabel("スイープ"), 0, 6)
        grid.addWidget(QLabel(""), 0, 7)

        self.ch_widgets = {}
        self.ch_labels = {}
        for row, ch in enumerate(self.scope.CHANNELS, start=1):
            on_chk = QCheckBox()
            on_chk.setChecked(True)
            vdiv = QComboBox()
            vdiv.addItems(VDIV_PRESETS)
            vdiv.setCurrentText(self.cfg.scope_vdiv)  # 標準 50 mV/div
            offset = QSpinBox()
            offset.setRange(-10000, 10000)
            offset.setSingleStep(10)
            # 標準 +100 mV: 負パルスに合わせ表示範囲 -300〜+100 mV
            offset.setValue(self.cfg.scope_ch_offset_mv)
            cpl = QComboBox()
            cpl.addItems(COUPLINGS)   # 既定 D50: 高速信号の反射防止 (仕様書2章)
            cpl.setCurrentText(self.cfg.scope_coupling)
            avg_chk = QCheckBox()
            avg_chk.setToolTip(f"{ch} のプリプロセッシング平均 ON/OFF")
            avg_sweeps = QSpinBox()
            avg_sweeps.setRange(2, 1000000)
            avg_sweeps.setValue(100)
            avg_sweeps.setEnabled(False)  # 平均 OFF の間は無効
            # 上下ボタンは省略: Windows 11 スタイルは横並びの矢印で幅を
            # 取り、狭いカラムで数字が切れる (オフセットに幅を回す)
            avg_sweeps.setButtonSymbols(QAbstractSpinBox.NoButtons)
            avg_sweeps.setToolTip("平均するスイープ数")
            avg_chk.toggled.connect(avg_sweeps.setEnabled)
            apply_btn = QPushButton("適用")
            apply_btn.setMinimumWidth(56)
            apply_btn.clicked.connect(
                lambda _=False, c=ch: self._apply_channel(c))
            # 読み出しパッド対応 (C1=J8 等) は refresh_sensor で併記する
            self.ch_labels[ch] = QLabel(ch)
            grid.addWidget(self.ch_labels[ch], row, 0)
            grid.addWidget(on_chk, row, 1)
            grid.addWidget(vdiv, row, 2)
            grid.addWidget(offset, row, 3)
            grid.addWidget(cpl, row, 4)
            grid.addWidget(avg_chk, row, 5)
            grid.addWidget(avg_sweeps, row, 6)
            grid.addWidget(apply_btn, row, 7)
            self.ch_widgets[ch] = (on_chk, vdiv, offset, cpl,
                                   avg_chk, avg_sweeps)
        grid.setRowStretch(len(self.scope.CHANNELS) + 1, 1)
        return w

    def _build_save_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)

        save_dir_label = QLabel("保存先:")
        save_dir_label.setToolTip("オシロ本体側の保存先フォルダ")
        grid.addWidget(save_dir_label, 0, 0)
        self.save_dir_edit = QLineEdit(self._save_dir_default)
        grid.addWidget(self.save_dir_edit, 0, 1, 1, 3)
        prefix_label = QLabel("ファイル名:")
        prefix_label.setToolTip("保存ファイル名の先頭 (prefix)")
        grid.addWidget(prefix_label, 1, 0)
        self.prefix_edit = QLineEdit("aclgad")
        grid.addWidget(self.prefix_edit, 1, 1)
        grid.addWidget(QLabel("形式:"), 1, 2)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["Binary", "ASCII", "MATLAB"])
        grid.addWidget(self.fmt_combo, 1, 3)
        grid.addWidget(QLabel("対象:"), 2, 0)
        self.src_combo = QComboBox()
        self.src_combo.addItems(["AllDisplayed", "C1", "C2", "C3", "C4"])
        grid.addWidget(self.src_combo, 2, 1)
        setup_btn = QPushButton("保存設定を送信")
        setup_btn.clicked.connect(self._apply_save_setup)
        grid.addWidget(setup_btn, 2, 2, 1, 2)

        save_now_btn = QPushButton("今すぐ保存")
        save_now_btn.clicked.connect(
            lambda: self._call(self.scope.save_waveform_now))
        grid.addWidget(save_now_btn, 3, 0)
        grid.addWidget(QLabel("オートセーブ:"), 3, 1)
        self.autosave_combo = QComboBox()
        self.autosave_combo.addItems(["Off", "Fill", "Wrap"])
        grid.addWidget(self.autosave_combo, 3, 2)
        autosave_btn = QPushButton("適用")
        autosave_btn.clicked.connect(
            lambda: self._call(lambda: self.scope.set_autosave(
                self.autosave_combo.currentText())))
        grid.addWidget(autosave_btn, 3, 3)

        grid.addWidget(QLabel("本体設定メモリ:"), 4, 0)
        self.setup_slot = QSpinBox()
        self.setup_slot.setRange(1, 6)
        grid.addWidget(self.setup_slot, 4, 1)
        sav_btn = QPushButton("保存 (*SAV)")
        sav_btn.clicked.connect(
            lambda: self._call(lambda: self.scope.save_panel_setup(
                self.setup_slot.value())))
        rcl_btn = QPushButton("呼出 (*RCL)")
        rcl_btn.clicked.connect(
            lambda: self._call(lambda: self.scope.recall_panel_setup(
                self.setup_slot.value())))
        grid.addWidget(sav_btn, 4, 2)
        grid.addWidget(rcl_btn, 4, 3)
        grid.setRowStretch(5, 1)
        return w

    def _build_console_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        row = QHBoxLayout()
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText(
            "リモートコマンド (例: TRMD?, VBS? 'return=app.Acquisition.TriggerMode')")
        self.cmd_edit.returnPressed.connect(self._send_query)
        write_btn = QPushButton("送信")
        write_btn.setToolTip("応答を待たない (設定コマンド用)")
        write_btn.clicked.connect(self._send_write)
        query_btn = QPushButton("クエリ")
        query_btn.setToolTip("応答を待つ (?コマンド用)。結果はログに表示")
        query_btn.clicked.connect(self._send_query)
        row.addWidget(self.cmd_edit, 1)
        row.addWidget(write_btn)
        row.addWidget(query_btn)
        layout.addLayout(row)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # 接続
    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.scope.connected:
            self._stop_mirror()
            self.caller.call(self.scope.disconnect,
                             on_result=lambda _: self._on_disconnected())
            return
        self.scope.resource = self.addr_edit.text().strip()
        self.connect_btn.setEnabled(False)
        self.caller.call(self.scope.connect,
                         on_result=lambda _: self._on_connected(),
                         on_error=self._on_conn_error)

    def _on_connected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("切断")
        self.conn_label.setText("接続中")
        self.conn_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        self.tabs.setEnabled(True)
        self.mirror_btn.setEnabled(True)

    def _on_disconnected(self):
        self.connect_btn.setText("接続")
        self.conn_label.setText("未接続")
        self.conn_label.setStyleSheet("color: gray;")
        self.tabs.setEnabled(False)
        self.mirror_btn.setEnabled(False)

    def _on_conn_error(self, exc: Exception):
        self.connect_btn.setEnabled(True)
        QMessageBox.warning(self, "オシロ接続エラー", str(exc))

    # ------------------------------------------------------------------
    # ミラーリング
    # ------------------------------------------------------------------
    def _toggle_mirror(self):
        if self._mirror is not None:
            self._stop_mirror()
        else:
            self._mirror = ScreenMirror(
                self.scope, self.mirror_interval.currentData())
            self._mirror.image_ready.connect(self._show_screen)
            self._mirror.error.connect(self._mirror_error)
            self._mirror.start()
            self.mirror_btn.setText("ミラー停止")

    def _stop_mirror(self):
        if self._mirror is not None:
            self._mirror.stop()
            self._mirror = None
        self.mirror_btn.setText("ミラー開始")

    def _mirror_interval_changed(self):
        if self._mirror is not None:
            self._mirror.interval_s = self.mirror_interval.currentData()

    def _show_screen(self, png: bytes):
        pix = QPixmap()
        if pix.loadFromData(png):
            self.screen_label.setPixmap(pix.scaled(
                self.screen_label.size(), Qt.KeepAspectRatio,
                Qt.SmoothTransformation))

    def _mirror_error(self, msg: str):
        self._stop_mirror()
        # 長いエラー文でパネルが広がらないよう折り返して表示する
        self.screen_label.setWordWrap(True)
        self.screen_label.setText(f"ミラーエラー: {msg}")

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _call(self, fn):
        """接続確認付きの非同期呼び出し (共通経路)。"""
        if not self.scope.connected:
            return
        self.caller.call(fn, on_error=self._show_error)

    def _apply_trigger(self):
        src = self.trig_src.currentText()
        level = f"{self.trig_level.value()}MV"
        slope = self.trig_slope.currentText()
        coupling = self.trig_coupling.currentText()
        self._call(lambda: self.scope.set_edge_trigger(
            src, level, slope, coupling))

    def _apply_horizontal(self):
        tdiv = self.tdiv_combo.currentText()
        delay = f"{self.trdl_spin.value():g}NS"

        def apply():
            self.scope.set_tdiv(tdiv)
            self.scope.set_trigger_delay(delay)
        self._call(apply)

    def _apply_standard_defaults(self):
        """測定標準条件 (config の scope_* 一式) を実機へ一括送信する。

        UI の表示値も標準値へ戻すことで、画面と実機設定のずれを防ぐ。
        """
        cfg = self.cfg
        self.trig_src.setCurrentText(cfg.scope_trig_source)
        self.trig_level.setValue(cfg.scope_trig_level_mv)
        self.trig_slope.setCurrentText(cfg.scope_trig_slope)
        self.trig_coupling.setCurrentText(cfg.scope_trig_coupling)
        self.tdiv_combo.setCurrentText(cfg.scope_tdiv)
        self.trdl_spin.setValue(
            float(cfg.scope_trig_delay.upper().replace("NS", "")))
        # 平均 (Averaging) は標準条件で必ず OFF: リアルタイム波形にしないと
        # SINGLE/シーケンス取得の完了判定が機能しない (2026-07 実機確認)
        for ch in self.scope.CHANNELS:
            on_chk, vdiv, offset, cpl, avg_chk, _avg_sweeps = \
                self.ch_widgets[ch]
            on_chk.setChecked(True)
            vdiv.setCurrentText(cfg.scope_vdiv)
            offset.setValue(cfg.scope_ch_offset_mv)
            cpl.setCurrentText(cfg.scope_coupling)
            avg_chk.setChecked(False)

        def apply():
            self.scope.set_edge_trigger(
                cfg.scope_trig_source, f"{cfg.scope_trig_level_mv}MV",
                cfg.scope_trig_slope, cfg.scope_trig_coupling)
            self.scope.set_tdiv(cfg.scope_tdiv)
            self.scope.set_trigger_delay(cfg.scope_trig_delay)
            for ch in self.scope.CHANNELS:
                self.scope.set_channel(
                    ch, on=True, vdiv=cfg.scope_vdiv,
                    offset=f"{cfg.scope_ch_offset_mv}MV",
                    coupling=cfg.scope_coupling)
            self.scope.set_averaging_off()
        self._call(apply)

    def _apply_sequence(self):
        on = self.seq_check.isChecked()
        n = self.seq_segments.value()
        self._call(lambda: self.scope.set_sequence(on, n))

    def _apply_channel(self, ch: str):
        on_chk, vdiv, offset, cpl, avg_chk, avg_sweeps = self.ch_widgets[ch]
        on = on_chk.isChecked()
        vdiv_v = vdiv.currentText()
        offset_v = f"{offset.value()}MV"
        cpl_v = cpl.currentText()
        # 平均 OFF は AverageSweeps=1 (平均なし) に相当
        sweeps = avg_sweeps.value() if avg_chk.isChecked() else 1

        def apply():
            self.scope.set_channel(ch, on=on, vdiv=vdiv_v,
                                   offset=offset_v, coupling=cpl_v)
            self.scope.set_channel_average(ch, sweeps)
        self._call(apply)

    def _apply_save_setup(self):
        directory = self.save_dir_edit.text().strip()
        prefix = self.prefix_edit.text().strip() or "aclgad"
        fmt = self.fmt_combo.currentText()
        source = self.src_combo.currentText()
        self._call(lambda: self.scope.configure_waveform_save(
            directory, prefix, source=source, fmt=fmt))

    def _send_write(self):
        cmd = self.cmd_edit.text().strip()
        if cmd:
            self._call(lambda: self.scope.write(cmd))

    def _send_query(self):
        cmd = self.cmd_edit.text().strip()
        if cmd:
            # 応答はデバイスの log 経由でログパネルに表示される
            self._call(lambda: self.scope.query(cmd))

    def _show_error(self, exc: Exception):
        QMessageBox.warning(self, "オシロエラー", str(exc))

    # ------------------------------------------------------------------
    def emergency_stop(self):
        if self.scope.connected:
            self.caller.call(self.scope.stop_acquisition)

    def shutdown(self):
        self._stop_mirror()
        if self.scope.connected:
            try:
                self.scope.disconnect()
            except Exception:
                pass
        self.caller.shutdown()
