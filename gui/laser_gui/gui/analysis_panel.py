# -*- coding: utf-8 -*-
"""解析タブ: 取得済みスキャンデータの簡易解析を GUI から実行する。

analysis\\run_analysis.py をサブプロセス (QProcess) として起動する方式:
  - GUI スレッドをブロックしない (測定・スキャン中でも解析できる)
  - matplotlib / multiprocessing を GUI プロセスから隔離できる
  - いつでも中断 (kill) できる

機能:
  - data\\ 以下のスキャンフォルダ一覧 (新しい順) から選択、または参照
  - スキャンの種類 (1dX / 1dY / 1dZ / 2dYX / tune / 1d_tune) を自動判別して、種類ごとの
    解析コードを実行 (run_analysis 側)。必要なら手動指定も可。定義は解析ノート matome と同じ
  - ROOT 変換 (--root / --no-raw)・キャッシュ再計算 (--force)・
    しきい値 [mV] などのオプション
  - 実行ログ表示 + reduce/変換の進捗バー
  - 完了後: 生成された図 (PNG) をその場でプレビュー、結果フォルダを開く

出力先は解析パッケージの仕様どおりリポジトリ外
(results\\<スキャン名>\\ とスキャンフォルダ隣の .root)。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtGui import QFont, QPixmap, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QVBoxLayout,
                               QWidget)

# laser_gui_v2\gui\ → laser_gui_v2\analysis\
ANALYSIS_DIR = Path(__file__).resolve().parents[1] / "analysis"


class AnalysisPanel(QWidget):
    """スキャンデータ簡易解析の実行パネル (解析タブの中身)。"""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._proc: QProcess | None = None
        self._results_dir: Path | None = None
        self._preview_pixmap: QPixmap | None = None

        root = QHBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split)

        # ============ 左: 条件・実行・ログ ============
        left = QWidget()
        lv = QVBoxLayout(left)

        # --- スキャン選択 ---
        box = QGroupBox("スキャンデータ選択")
        g = QGridLayout(box)
        self.scan_combo = QComboBox()
        self.scan_combo.setMinimumWidth(280)
        g.addWidget(self.scan_combo, 0, 0, 1, 2)
        btn_refresh = QPushButton("更新")
        btn_refresh.clicked.connect(self.refresh_scans)
        g.addWidget(btn_refresh, 0, 2)
        btn_browse = QPushButton("参照...")
        btn_browse.clicked.connect(self._browse_scan)
        g.addWidget(btn_browse, 0, 3)
        self.scan_info = QLabel("-")
        self.scan_info.setWordWrap(True)
        g.addWidget(self.scan_info, 1, 0, 1, 4)
        lv.addWidget(box)

        # --- オプション ---
        box = QGroupBox("解析オプション")
        g = QGridLayout(box)
        g.addWidget(QLabel("モード:"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["自動判別", "1dX", "1dY", "1dZ", "2dYX", "tune", "1d_tune", "pixel"])
        self.mode_combo.setToolTip(
            "スキャンの種類ごとに解析コードが分かれています:\n"
            "1dX 長さ方向 (Elmore 遅延など) / 1dY 横方向 (位置分解能・v_eff など) / 1dZ フォーカス\n"
            "2dYX 全項目 / tune 振幅依存 (1/A 則) / 1d_tune 位置 × 強度\n"
            "pixel ピクセル 2x2 読み出し (ログの sensor=pixel... で自動判別。2/3/4 ピクセル組み合わせの時間・位置分解能)")
        g.addWidget(self.mode_combo, 0, 1)
        self.chk_force = QCheckBox("キャッシュ再計算 (--force)")
        g.addWidget(self.chk_force, 0, 2, 1, 2)

        self.chk_template = QCheckBox("テンプレートフィット解析 (論文図 01–09 / ピクセル pix01–06) も行う")
        self.chk_template.setChecked(True)
        self.chk_template.setToolTip(
            "2dYX: macro\\template_timing\\tt_pipeline.py で .trc からテンプレートフィット (CFD50) → 時間分解能 (最適手法)、\n"
            "較正なし振幅重心の位置分解能、振幅分布、立ち上がり、ジッター、伝搬の論文図 01–09 を results\\<scan>\\paper_plots\\ に作る\n"
            "(results\\paper_plots_20261013 と同じ内容。2000 セグメント × 16000 点で約 30 分、2 回目以降は数分)。\n"
            "pixel: tt_pixel.py (2/3/4 ピクセル合成)。OFF: 簡易解析 (線形 CFD) の図のみ")
        g.addWidget(self.chk_template, 1, 0, 1, 4)

        self.chk_root = QCheckBox("ROOT 変換も行う")
        self.chk_root.toggled.connect(
            lambda on: self.chk_noraw.setEnabled(on))
        g.addWidget(self.chk_root, 2, 0, 1, 2)
        self.chk_noraw = QCheckBox("生波形なし (特徴量のみ)")
        self.chk_noraw.setEnabled(False)
        g.addWidget(self.chk_noraw, 2, 2, 1, 2)

        def _thr(v):
            s = QDoubleSpinBox()
            s.setRange(0.1, 500.0)
            s.setDecimals(1)
            s.setValue(v)
            s.setSuffix(" mV")
            return s
        # 既定値は解析ノート (matome) と同じ: hit = max(8 mV, 5σ)、品質 = ノイズ < max(4 mV, 3σ)
        g.addWidget(QLabel("ヒット閾値:"), 3, 0)
        self.spin_hit = _thr(8.0)
        self.spin_hit.setToolTip("hit の振幅下限。実際の閾値は max(この値, 5 × 全体ノイズ基準)")
        g.addWidget(self.spin_hit, 3, 1)
        g.addWidget(QLabel("ノイズ上限:"), 3, 2)
        self.spin_noise = _thr(4.0)
        self.spin_noise.setToolTip("品質カット: ベースライン RMS < max(この値, 3 × 全体ノイズ基準)")
        g.addWidget(self.spin_noise, 3, 3)
        g.addWidget(QLabel("ToT閾値:"), 4, 0)
        self.spin_tot = _thr(10.0)
        g.addWidget(self.spin_tot, 4, 1)
        g.addWidget(QLabel("並列数:"), 4, 2)
        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 32)
        self.spin_workers.setValue(8)
        g.addWidget(self.spin_workers, 4, 3)

        def _um(v, lo=-50000.0, hi=50000.0):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(0)
            s.setSingleStep(10.0)
            s.setValue(v)
            s.setSuffix(" um")
            return s
        # センサー座標 (X = 0 が読み出し側の外枠エッジ) へのオフセット。現行の GUI で取ったスキャンは 0
        g.addWidget(QLabel("X オフセット:"), 5, 0)
        self.spin_xoff = _um(0.0)
        self.spin_xoff.setToolTip("ログ座標 → センサー座標。2026-09-07 より前のスキャンは -632 など")
        g.addWidget(self.spin_xoff, 5, 1)
        g.addWidget(QLabel("Y オフセット:"), 5, 2)
        self.spin_yoff = _um(0.0)
        g.addWidget(self.spin_yoff, 5, 3)
        g.addWidget(QLabel("ピッチ:"), 6, 0)
        self.spin_pitch = _um(500.0, 50.0, 5000.0)
        g.addWidget(self.spin_pitch, 6, 1)
        g.addWidget(QLabel("本体 X 範囲:"), 6, 2)
        hb = QHBoxLayout()
        self.spin_body_lo = _um(-9900.0)
        self.spin_body_hi = _um(-700.0)
        for w_ in (self.spin_body_lo, self.spin_body_hi):
            w_.setToolTip("長さ方向のフィットと代表値に使う X 範囲 (パッド近傍と遠端の先を除く)")
            hb.addWidget(w_)
        g.addLayout(hb, 6, 3)
        lv.addWidget(box)

        # --- 実行・進捗 ---
        row = QHBoxLayout()
        self.btn_run = QPushButton("解析開始")
        self.btn_run.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white;"
            " font-weight: bold; padding: 5px 14px; }")
        self.btn_run.clicked.connect(self.start_analysis)
        row.addWidget(self.btn_run)
        self.btn_abort = QPushButton("中断")
        self.btn_abort.setEnabled(False)
        self.btn_abort.clicked.connect(self.abort_analysis)
        row.addWidget(self.btn_abort)
        self.btn_open = QPushButton("結果フォルダを開く")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_results)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        lv.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        lv.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(8)
        self.log_view.setFont(f)
        lv.addWidget(self.log_view, 1)

        split.addWidget(left)

        # ============ 右: 図プレビュー ============
        right = QWidget()
        rv = QVBoxLayout(right)
        row = QHBoxLayout()
        row.addWidget(QLabel("図:"))
        self.fig_combo = QComboBox()
        self.fig_combo.currentIndexChanged.connect(self._show_figure)
        row.addWidget(self.fig_combo, 1)
        rv.addLayout(row)
        self.fig_label = QLabel("解析を実行すると図がここに表示されます")
        self.fig_label.setAlignment(Qt.AlignCenter)
        self.fig_scroll = QScrollArea()
        self.fig_scroll.setWidget(self.fig_label)
        self.fig_scroll.setWidgetResizable(True)
        rv.addWidget(self.fig_scroll, 1)
        split.addWidget(right)
        split.setSizes([460, 640])

        self.refresh_scans()

    # ------------------------------------------------------------------
    # スキャンフォルダ一覧
    # ------------------------------------------------------------------
    def _data_root(self) -> Path:
        """回収先データルート (config.transfer_dest_dir, 既定 ..\\data)。"""
        p = Path(self.cfg.transfer_dest_dir)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[1] / p
        return p.resolve()

    def refresh_scans(self):
        """data\\ 以下の scan_* フォルダを新しい順に一覧へ載せる。"""
        current = self.scan_combo.currentData()
        self.scan_combo.clear()
        root = self._data_root()
        scans = sorted(root.glob("*/*/scan_*"),
                       key=lambda p: p.name, reverse=True)
        scans = [s for s in scans if s.is_dir()]
        for s in scans:
            try:
                label = str(s.relative_to(root))
            except ValueError:
                label = s.name
            self.scan_combo.addItem(label, str(s))
        if current:
            i = self.scan_combo.findData(current)
            if i >= 0:
                self.scan_combo.setCurrentIndex(i)
        self.scan_info.setText(
            f"データルート: {root}  ({len(scans)} スキャン)")

    def _browse_scan(self):
        d = QFileDialog.getExistingDirectory(
            self, "スキャンフォルダを選択", str(self._data_root()))
        if d:
            self.scan_combo.insertItem(0, d, d)
            self.scan_combo.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # 実行
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return (self._proc is not None
                and self._proc.state() != QProcess.NotRunning)

    def start_analysis(self):
        if self.is_running():
            return
        scan_path = self.scan_combo.currentData()
        if not scan_path:
            QMessageBox.warning(self, "解析開始不可",
                                "スキャンフォルダを選択してください")
            return
        if not (ANALYSIS_DIR / "run_analysis.py").exists():
            QMessageBox.warning(self, "解析開始不可",
                                f"解析スクリプトが見つかりません:\n"
                                f"{ANALYSIS_DIR}\\run_analysis.py")
            return

        args = ["-u", "run_analysis.py", scan_path,
                "--workers", str(self.spin_workers.value()),
                "--hit-thr", f"{self.spin_hit.value():g}",
                "--noise-max", f"{self.spin_noise.value():g}",
                "--tot-thr", f"{self.spin_tot.value():g}",
                "--x-off", f"{self.spin_xoff.value():g}",
                "--y-off", f"{self.spin_yoff.value():g}",
                "--pitch", f"{self.spin_pitch.value():g}",
                "--body-x", f"{self.spin_body_lo.value():g}", f"{self.spin_body_hi.value():g}"]
        if self.mode_combo.currentIndex() > 0:
            args += ["--mode", self.mode_combo.currentText()]
        if self.chk_force.isChecked():
            args.append("--force")
        if not self.chk_template.isChecked():
            args.append("--no-template")
        if self.chk_root.isChecked():
            args.append("--root")
            if self.chk_noraw.isChecked():
                args.append("--no-raw")

        self._results_dir = None
        self.log_view.clear()
        self._append_log(f"$ python {' '.join(args[1:])}\n")
        self.progress.setRange(0, 0)      # busy 表示 (進捗行が来たら確定値)

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(str(ANALYSIS_DIR))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start(sys.executable, args)

        self.btn_run.setEnabled(False)
        self.btn_abort.setEnabled(True)

    def abort_analysis(self):
        if self.is_running():
            self._append_log("\n★ 中断要求 (プロセスを終了します) ★\n")
            self._proc.kill()

    def shutdown(self):
        """GUI 終了時の後始末 (実行中プロセスを止める)。"""
        if self.is_running():
            self._proc.kill()
            self._proc.waitForFinished(2000)

    # ------------------------------------------------------------------
    # プロセス出力・終了処理
    # ------------------------------------------------------------------
    _RE_PROGRESS = re.compile(
        r"(?:reduce\s+(\d+)/(\d+)|(\d+)/(\d+)\s+points)")

    def _append_log(self, text: str):
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.insertPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_output(self):
        if self._proc is None:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", errors="replace")
        self._append_log(text)
        for line in text.splitlines():
            m = self._RE_PROGRESS.search(line)
            if m:
                done, total = (m.group(1), m.group(2)) if m.group(1) \
                    else (m.group(3), m.group(4))
                self.progress.setRange(0, int(total))
                self.progress.setValue(int(done))
            elif line.startswith("results"):
                # run_analysis の "results  : <path>" 行から結果先を拾う
                path = line.split(":", 1)[1].strip()
                if path:
                    self._results_dir = Path(path)

    def _on_error(self, err):
        if self._proc is not None \
                and self._proc.state() == QProcess.NotRunning:
            self._append_log(f"\n[プロセスエラー] {err}\n")

    def _on_finished(self, code: int, status):
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if code == 0 else 0)
        self.btn_run.setEnabled(True)
        self.btn_abort.setEnabled(False)
        if code == 0:
            self._append_log("\n=== 解析完了 ===\n")
        else:
            self._append_log(f"\n=== 終了 (code {code}) ===\n")
        if self._results_dir and self._results_dir.is_dir():
            self.btn_open.setEnabled(True)
            self._load_figures()

    # ------------------------------------------------------------------
    # 結果 (図プレビュー)
    # ------------------------------------------------------------------
    def _load_figures(self):
        self.fig_combo.blockSignals(True)
        self.fig_combo.clear()
        pngs = sorted(self._results_dir.glob("*.png"))
        for p in pngs:
            self.fig_combo.addItem(p.name, str(p))
        self.fig_combo.blockSignals(False)
        if pngs:
            self.fig_combo.setCurrentIndex(0)
            self._show_figure(0)

    def _show_figure(self, _index: int):
        path = self.fig_combo.currentData()
        if not path:
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.fig_label.setText(f"読み込み失敗: {path}")
            return
        self._preview_pixmap = pm
        self._rescale_figure()

    def _rescale_figure(self):
        if self._preview_pixmap is None:
            return
        w = max(self.fig_scroll.viewport().width() - 4, 100)
        self.fig_label.setPixmap(self._preview_pixmap.scaledToWidth(
            min(w, self._preview_pixmap.width()),
            Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_figure()

    def _open_results(self):
        if self._results_dir and self._results_dir.is_dir():
            os.startfile(str(self._results_dir))  # noqa: S606 (Windows)
