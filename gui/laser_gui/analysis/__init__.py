# -*- coding: utf-8 -*-
"""laser_gui_v2 取得データの簡易解析パッケージ。

スキャンフォルダ (data\\<年>\\<YYYYMMDD>\\scan_<日時>_<タグ>) と
GUI スキャンログ CSV (laser_gui_v2\\logs\\scan_<日時>.csv) を入力に、
スキャンの種類 (1dX / 1dY / 1dZ / 2dYX / tune / 1d_tune) ごとの解析図・数値
(解析ノート matome と同じ定義) と、.trc → 単一 ROOT ファイル変換を行う。

使い方は README.md と run_analysis.py --help を参照。
出力 (ROOT・図・CSV) はリポジトリの外 (data\\... と results\\...) に置く。
"""
