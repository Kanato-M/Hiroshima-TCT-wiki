# -*- coding: utf-8 -*-
"""説明書用のスクリーンショットをパネル・タブ・グループごとに書き出す。

使い方 (laser_gui_v4 ディレクトリで):
    python docs\\make_screenshots.py                # docs\\screenshots\\ へ出力
    python docs\\make_screenshots.py --out <dir>    # 出力先を指定

全デバイスをシミュレーションモードで接続した状態 (位置表示・レーザー
状態・模擬カメラ画像が入った状態) で GUI を起動し、
  - ウィンドウ全体 / ツールバー / ステータスバー / [制御]・[解析] タブ
  - 各パネル (オシロ・通信ログ・カメラ・ステージ・レーザー・自動スキャン・
    解析) と、その中の各タブ (タブを切り替えて撮影) ・各グループボックス
  - レーザー発振 ON 表示、センサー編集ダイアログ (ストリップ / ピクセル)
を個別の PNG に保存する。一覧は同じフォルダの INDEX.md に書く。
画像は main.py と同じスケール補正 (高 DPI 環境では 2 倍以上の画素数) で
描画されるので、そのまま説明書に貼れる。実機・logs/ には触れない
(シミュレーションは logs_sim/ を使う)。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_DIR))

import main  # noqa: E402,F401  (main.py と同じ QT_SCALE_FACTOR 補正を適用)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (QApplication, QGroupBox, QMessageBox,  # noqa: E402
                               QTabWidget, QToolBar, QWidget)

from config import GuiConfig  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.sensor_dialog import SensorDialog  # noqa: E402


def pump(app: QApplication, seconds: float) -> None:
    """イベントループを回しながら待つ (非同期の接続・ポーリング用)。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def slug(text: str, fallback: str) -> str:
    """ファイル名用の ASCII 化 (日本語などは落とす。残らなければ fallback)。"""
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or fallback


class Shooter:
    def __init__(self, app: QApplication, out: Path):
        self.app = app
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.rows: list[tuple[str, str, str]] = []
        self.n = 0

    def shot(self, widget: QWidget, name: str, title: str) -> None:
        self.app.processEvents()
        pm = widget.grab()
        self.n += 1
        fname = f"{self.n:02d}_{name}.png"
        pm.save(str(self.out / fname))
        self.rows.append((fname, title, f"{pm.width()}×{pm.height()}"))
        print(f"  {fname:40s} {title}")

    def panel_with_parts(self, panel: QWidget, name: str, title: str) -> None:
        """パネル全体 → 各タブ (切り替えて撮影) → 各グループボックス。"""
        self.shot(panel, name, title)
        for tabs in panel.findChildren(QTabWidget):
            keep = tabs.currentIndex()
            for i in range(tabs.count()):
                tabs.setCurrentIndex(i)
                self.app.processEvents()
                tab_title = tabs.tabText(i)
                tab_id = f"tab{i + 1}_{slug(tab_title, 'tab')}"
                self.shot(panel, f"{name}_{tab_id}",
                          f"{title} — [{tab_title}] タブ")
                page = tabs.widget(i)
                for k, box in enumerate(page.findChildren(QGroupBox), 1):
                    if box.title():
                        self.shot(box, f"{name}_{tab_id}_{slug(box.title(), f'box{k}')}",
                                  f"{title} — [{tab_title}] — 「{box.title()}」")
            tabs.setCurrentIndex(keep)
        # タブの外にあるグループボックス
        in_tabs = set()
        for tabs in panel.findChildren(QTabWidget):
            for i in range(tabs.count()):
                in_tabs.update(id(b) for b in tabs.widget(i).findChildren(QGroupBox))
        for k, box in enumerate(panel.findChildren(QGroupBox), 1):
            if box.title() and id(box) not in in_tabs:
                self.shot(box, f"{name}_{slug(box.title(), f'box{k}')}",
                          f"{title} — 「{box.title()}」")

    def write_index(self, header: str) -> None:
        lines = [header, "", "| ファイル | 対象 | 画素数 |", "|---|---|---|"]
        for fname, title, size in self.rows:
            lines.append(f"| `{fname}` | {title} | {size} |")
        (self.out / "INDEX.md").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")


def run(out: Path) -> int:
    cfg = GuiConfig.load()
    cfg.force_simulate()
    app = QApplication(sys.argv)
    win = MainWindow(cfg)
    win.show()
    pump(app, 0.8)

    # --- 全デバイスをシミュレーションで接続 (接続後の表示にする) ---
    win.stage_panel._home_reminded = True   # 原点復帰リマインダ (モーダル) を抑止
    win.scope_panel._toggle_connect()
    win.stage_panel._toggle_connect()
    win.laser_panel._toggle_connect()
    win.camera_panel._toggle()
    pump(app, 3.0)      # 接続・最初のポーリング・カメラ画像を待つ
    win.scope_panel._toggle_mirror()         # 波形ミラーを開始
    pump(app, 2.5)

    sh = Shooter(app, out)
    print(f"出力先: {out}")

    # --- 全体・ツールバー・ステータスバー・メインタブ ---
    sh.shot(win, "main_window", "ウィンドウ全体 ([制御] タブ)")
    toolbar = win.findChild(QToolBar)
    sh.shot(toolbar, "toolbar", "ツールバー (センサー選択 / タスク進捗・中断 / 全停止)")
    sh.shot(win.statusBar(), "statusbar", "ステータスバー (センサー・バイアス・読み出し対応)")
    main_tabs = win.centralWidget()
    sh.shot(main_tabs.tabBar(), "main_tabs", "[制御] / [解析] タブ")

    # --- 各パネル ---
    sh.panel_with_parts(win.scope_panel, "scope", "オシロスコープパネル")
    sh.panel_with_parts(win.log_panel, "log", "通信ログパネル")
    sh.panel_with_parts(win.camera_panel, "camera", "フォーカスカメラパネル")
    sh.panel_with_parts(win.stage_panel, "stage", "ステージパネル")
    sh.panel_with_parts(win.laser_panel, "laser", "レーザーパネル")
    # 発振 ON 表示 (シミュレーション)
    win.laser_panel._set_emission(True)
    pump(app, 1.0)
    sh.shot(win.laser_panel, "laser_emission_on", "レーザーパネル — 発振 ON 表示")
    win.laser_panel._set_emission(False)
    pump(app, 1.0)
    sh.panel_with_parts(win.scan_panel, "scan", "自動スキャンパネル")

    # --- 解析タブ ---
    main_tabs.setCurrentIndex(1)
    pump(app, 0.3)
    sh.shot(win, "main_window_analysis", "ウィンドウ全体 ([解析] タブ)")
    sh.panel_with_parts(win.analysis_panel, "analysis", "解析パネル")
    main_tabs.setCurrentIndex(0)

    # --- センサー編集ダイアログ (ストリップ / ピクセル) ---
    dlg = SensorDialog(win.registry, win)
    dlg.setAttribute(Qt.WA_DontShowOnScreen, False)
    dlg.show()
    pump(app, 0.5)
    sh.shot(dlg, "sensor_dialog_strip", "センサー編集ダイアログ (ストリップ型)")
    idx = dlg.type_combo.findData("pixel")
    if idx >= 0:
        dlg.type_combo.setCurrentIndex(idx)
        pump(app, 0.3)
        sh.shot(dlg, "sensor_dialog_pixel", "センサー編集ダイアログ (ピクセル型)")
    dlg.reject()

    sh.write_index(
        f"# 説明書用スクリーンショット一覧 ({PKG_DIR.name})\n\n"
        "`docs/make_screenshots.py` がシミュレーションモードで自動生成。"
        "全デバイス接続後の表示。")
    print(f"{sh.n} 枚を書き出しました → {out / 'INDEX.md'}")

    # --- 終了 (closeEvent の終了確認ダイアログは自動で「はい」にする) ---
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    win.close()
    pump(app, 0.5)
    return 0


def main_cli() -> int:
    parser = argparse.ArgumentParser(
        description="説明書用スクリーンショット (パネル・タブ・グループ別) を生成")
    parser.add_argument("--out", default=str(PKG_DIR / "docs" / "screenshots"),
                        help="出力ディレクトリ (既定: docs/screenshots)")
    args = parser.parse_args()
    return run(Path(args.out))


if __name__ == "__main__":
    sys.exit(main_cli())
