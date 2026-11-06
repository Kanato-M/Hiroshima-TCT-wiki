# -*- coding: utf-8 -*-
"""取得データ簡易解析のエントリポイント (スキャンの種類を自動判別して解析を振り分ける)。

解析ノート matome (laser_test/analysis/results/mip/combined/matome) と同じ定義で、スキャンの
種類ごとにできる解析項目を実行する:

  種類      スクリプト            解析項目
  1dX       analyze_1dx.py        長さ方向: 振幅・電荷、立ち上がり、ジッター、時間分解能、Elmore 遅延
  1dY       analyze_1dy.py        横方向: 振幅・電荷、立ち上がり、ジッター、位置分解能 (η)、時間分解能、v_eff
  1dZ       analyze_1dz.py        フォーカス走査の最適 Z
  2dYX      analyze_2dyx.py       項目 1–7 すべて (マップ、ギャップ内・長さ方向、位置分解能、伝搬)
                                  + テンプレートフィット解析の論文図 01–09 (macro\template_timing\tt_pipeline.py、--no-template で省略)
  tune      analyze_tune.py       振幅依存: MIP 相当の TUNE、ジッターの 1/A 則、σ_t 対振幅
  1d_tune   analyze_1d_tune.py    位置 × 強度: 1/A 則、σ_t 対振幅、位置分解能の強度依存
  pixel     analyze_pixel.py      ピクセル (2x2 ch) の 2D: 振幅マップ、2/3/4 ピクセル組み合わせの時間・位置分解能、伝搬
                                  (ログの sensor=pixel... で自動判別。本解析は macro\template_timing\tt_pixel.py)

usage:
  python run_analysis.py <scan_dir または スキャン名> [オプション]

例:
  python run_analysis.py ..\\..\\data\\2026\\20260826\\scan_20260826_155255_1dX
  python run_analysis.py scan_20260824_202544_2dYX --x-off -632
  python run_analysis.py scan_20260707_005415_1dYtune --force

出力:
  解析図・summary.txt・numbers.json・summary.csv : <workspace>\\results\\<スキャン名>\\
  平均波形 ROOT : <workspace>\\results\\<スキャン名>\\mean_waveforms.root
  ROOT ファイル : <スキャンフォルダ>.root (data\\... 内, --root 時)

時間の目安 (8 並列): 縮約は 1 点あたり約 0.03 秒 (1D 100 点で約 10 秒、2D 2000 点で約 1 分)。
縮約結果は reduced.npz に残るので、2 回目以降は図の作り直しだけで数秒〜20 秒。
"""
from __future__ import annotations

import os

# 並列ワーカーの中で BLAS がスレッドを立てないようにする (numpy の import より前)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from features import FeatureConfig  # noqa: E402
from reduce_scan import load_reduced  # noqa: E402
from scan_data import load_scan, resolve_scan_dir  # noqa: E402

KINDS = ("1dX", "1dY", "1dZ", "2dYX", "tune", "1d_tune", "pixel")
MODULES = {"1dX": "analyze_1dx", "1dY": "analyze_1dy", "1dZ": "analyze_1dz", "2dYX": "analyze_2dyx",
           "tune": "analyze_tune", "1d_tune": "analyze_1d_tune", "pixel": "analyze_pixel"}


def sensor_name(scan) -> str:
    """スキャンログのヘッダの sensor=... (無ければ空)。"""
    for ln in scan.header.get("comments", []):
        for tok in str(ln).split():
            if tok.startswith("sensor="):
                return tok.split("=", 1)[1]
    return ""


def sensor_type(scan) -> str:
    """スキャンログのヘッダの sensor_type=... (strip / pixel)。無ければセンサー名の先頭 (pixel...) から推定。"""
    for ln in scan.header.get("comments", []):
        for tok in str(ln).split():
            if tok.startswith("sensor_type="):
                return tok.split("=", 1)[1].lower()
    return "pixel" if sensor_name(scan).lower().startswith("pixel") else "strip"


def scan_kind(scan) -> str:
    """スキャンログの mode とセンサーの種類 (sensor_type=pixel) から解析の種類を決める。"""
    mode = scan.mode
    if mode in ("tune", "1d_tune"):
        return mode
    if mode == "2d" and sensor_type(scan) == "pixel":
        return "pixel"
    if mode == "2d":
        axes = {scan.axis_letter(0), scan.axis_letter(1)}
        if axes != {"X", "Y"}:
            raise SystemExit(f"2D スキャンの軸が {sorted(axes)} です。簡易解析は X と Y の 2D (2dYX / 2dXY) のみ対応")
        return "2dYX"
    return {"X": "1dX", "Y": "1dY", "Z": "1dZ"}[scan.axis_letter(0)]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[2:]))
    ap.add_argument("scan", help="スキャンフォルダのパスまたは名前")
    ap.add_argument("--log", default=None, help="スキャンログ CSV (省略時 logs から自動検索)")
    ap.add_argument("--results", default=None, help="結果出力ルート (既定 <workspace>\\results)")
    ap.add_argument("--force", action="store_true", help="reduced キャッシュを作り直す")
    ap.add_argument("--workers", type=int, default=8, help="縮約処理の並列プロセス数 (既定 8)")
    ap.add_argument("--mode", default=None, choices=KINDS, help="解析の種類を強制指定 (既定: ログ/タグから自動)")
    ap.add_argument("--no-template", action="store_true",
                    help="2dYX / pixel のテンプレートフィット解析 (論文図、macro\\template_timing) を省く (簡易解析の図のみ)")
    ap.add_argument("--force-template", action="store_true", help="テンプレートフィット解析の全段階をやり直す")
    # 座標・幾何
    ap.add_argument("--x-off", type=float, default=0.0,
                    help="ログ座標 → センサー座標の X オフセット um (X=0 が読み出し側の外枠エッジ。"
                         "2026-09-07 より前のスキャンは -632 など)")
    ap.add_argument("--y-off", type=float, default=0.0, help="同 Y オフセット um")
    ap.add_argument("--pitch", type=float, default=500.0, help="ストリップピッチ um (既定 500)")
    ap.add_argument("--body-x", type=float, nargs=2, default=(-9900.0, -700.0), metavar=("LO", "HI"),
                    help="「本体」の X 範囲 um (パッド近傍と遠端の先を除く。既定 -9900 -700)")
    # ROOT 変換
    ap.add_argument("--root", action="store_true", help=".trc → 単一 ROOT ファイル変換も行う")
    ap.add_argument("--root-out", type=Path, default=None, help="ROOT 出力先 (既定 <scan_dir>.root)")
    ap.add_argument("--no-raw", action="store_true", help="ROOT に生波形を入れない (特徴量のみ)")
    ap.add_argument("--limit", type=int, default=None, help="ROOT 変換を先頭 N 点に制限 (動作確認用)")
    ap.add_argument("--no-meanwave-root", action="store_true",
                    help="各点の平均波形 ROOT (mean_waveforms.root) を出さない")
    # 特徴量の設定 (既定は解析ノートと同じ)
    ap.add_argument("--hit-thr", type=float, default=8.0,
                    help="hit の振幅下限 mV: max(これ, 5×全体ノイズ基準) (既定 8)")
    ap.add_argument("--noise-max", type=float, default=4.0,
                    help="品質カットのノイズ上限 mV: max(これ, 3×全体ノイズ基準) (既定 4)")
    ap.add_argument("--tot-thr", type=float, default=10.0, help="ToT 閾値 mV (既定 10)")
    ap.add_argument("--n-boot", type=int, default=30,
                    help="MPV 誤差のブートストラップ回数 (既定 30。解析ノートは 100。MPV の値は回数によらない)")
    args = ap.parse_args()

    cfg = FeatureConfig(hit_thr_mV=args.hit_thr, noise_abs_mV=args.noise_max, tot_thr_mV=args.tot_thr,
                        n_boot=args.n_boot)
    scan = load_scan(resolve_scan_dir(args.scan), args.log)
    kind = args.mode or scan_kind(scan)
    outdir = scan.results_dir(args.results)
    print(f"scan     : {scan.name}")
    print(f"kind     : {kind}  points={scan.n_points}  ch={','.join(scan.channels)}")
    print(f"log csv  : {scan.log_csv}")
    print(f"results  : {outdir}")
    if scan.log_csv is None:
        print("[warn] スキャンログ CSV が見つかりません。座標が分からないので位置依存の解析はできません (--log で指定)")

    red = load_reduced(scan, args.results, force=args.force, cfg=cfg, workers=args.workers)

    import importlib
    opts = {"x_off": args.x_off, "y_off": args.y_off, "pitch": args.pitch, "body_x": tuple(args.body_x),
            "workers": args.workers, "no_template": args.no_template, "force_template": args.force_template or args.force}
    importlib.import_module(MODULES[kind]).run(scan, red, outdir, opts)

    # 各測定点の平均波形を ROOT に書き出す (reduced.npz から。軽い)
    if not args.no_meanwave_root:
        try:
            import meanwave_root
            meanwave_root.write(scan, red, outdir)
        except ImportError as exc:
            print(f"[warn] mean_waveforms.root をスキップ ({exc})")

    if args.root:
        import trc_to_root
        trc_to_root.convert(scan, args.root_out, limit=args.limit, raw=not args.no_raw, cfg=cfg)

    print("完了")


if __name__ == "__main__":
    main()
