# -*- coding: utf-8 -*-
"""AC-LGAD レーザースキャン制御 GUI エントリポイント。

使い方:
    python main.py                 # config.py + config_local.json の設定で起動
    python main.py --simulate      # 全デバイスをシミュレーションで起動 (動作確認用)
    python main.py --config my.json

装置の電源を入れた後、このGUIから
  1. オシロ・ステージ・レーザー・カメラを接続
  2. カメラを見ながらステージで照射位置を移動
  3. レーザー発振 ON → オシロで波形確認・保存
が暗室外から行えることを目的とする。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# このファイルのディレクトリを import パスに追加 (どこから起動してもよいように)
sys.path.insert(0, str(Path(__file__).parent))

# GUI 全パネルが収まるのに必要な論理解像度。これより論理解像度が小さいと
# 最大化時にパネルが潰れて重なる (実験室の EX-LDHU431DB は 4K +
# Windows スケーリング 300% で論理 1280x720 しかないため補正が必須)。
_MIN_LOGICAL_W = 1600
_MIN_LOGICAL_H = 900


def _fit_scale_factor() -> float | None:
    """高DPI環境で論理解像度が不足する場合の Qt スケール補正係数を返す。

    Windows のスケーリング設定 (例: 300%) をそのまま使うと論理解像度が
    _MIN_LOGICAL_W/H を下回るとき、QT_SCALE_FACTOR (<1.0) で実効スケールを
    「画面に収まる最大値」まで下げる。文字などは物理解像度で描画される
    ため、鮮明さは変わらず一回り小さく表示されるだけになる。
    補正不要 (十分広い/等倍) なら None。
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # 物理ピクセルを取得するため先にプロセスを DPI aware にする
        # (Qt も後で同等の設定をするので副作用はない)
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PMv2
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        phys_w = user32.GetSystemMetrics(0)
        phys_h = user32.GetSystemMetrics(1)
        win_scale = user32.GetDpiForSystem() / 96.0
    except Exception:
        return None
    if phys_w <= 0 or phys_h <= 0 or win_scale <= 1.0:
        return None
    # この画面で許容できる最大スケール (これ以上大きいと GUI が収まらない)
    allowed = min(phys_w / _MIN_LOGICAL_W, phys_h / _MIN_LOGICAL_H)
    if allowed >= win_scale:
        return None  # Windows 設定のままで収まる
    # 100% (等倍) より小さくはしない
    return max(allowed, 1.0) / win_scale


_factor = _fit_scale_factor()
if _factor is not None and "QT_SCALE_FACTOR" not in os.environ:
    os.environ["QT_SCALE_FACTOR"] = f"{_factor:.3f}"

from PySide6.QtWidgets import QApplication  # noqa: E402

from config import GuiConfig  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AC-LGAD レーザースキャン制御 GUI")
    parser.add_argument("--simulate", action="store_true",
                        help="全デバイスをシミュレーションモードで起動")
    parser.add_argument("--config", default=None,
                        help="設定上書き用 JSON ファイルのパス")
    args = parser.parse_args()

    cfg = GuiConfig.load(args.config)
    if args.simulate:
        cfg.force_simulate()

    app = QApplication(sys.argv)
    window = MainWindow(cfg)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
