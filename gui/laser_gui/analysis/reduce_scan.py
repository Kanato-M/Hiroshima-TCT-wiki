# -*- coding: utf-8 -*-
"""Stage 1: スキャン全点を点ごとのサマリ量に縮約して npz に保存する。

各測定点の全 ch のシーケンス波形 (.trc) を読み、features.reduce_point で
  ch 別の量 (QUANT)、イベント単位の量 (EV_QUANT)、隣接ペアの量 (PAIR_QUANT)、
  η ヒストグラム、leading のイベント毎ジッター分布、平均波形
に縮約して results\\<スキャン名>\\reduced.npz にキャッシュする。per-segment の値は保存しない。
解析ステージ (analyze_*.py) はこの npz だけを読むので、図の作り直しは数秒で済む。

手順 (解析ノート matome の統一縮約と同じ):
  1. 事前パス: 点を約 24 個に間引いて読み、ch ごとの全体ノイズ基準 (ベースライン RMS の中央値) と
     パルスの極性を決める。品質カットと hit 判定の閾値はこの基準から決まる。
  2. 本パス: 全点を並列に縮約する。

単体実行: python reduce_scan.py <scan_dir> [--force]
"""
from __future__ import annotations

import os

# 並列ワーカーの中で BLAS がさらにスレッドを立てると遅くなるので 1 スレッドに固定する (numpy の import より前)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import (ETA_NBIN, EV_QUANT, JIT_MAX_PS, JIT_NBIN, PAIR_QUANT, QUANT,  # noqa: E402
                      FeatureConfig, detect_polarity, reduce_point, segment_features)
from scan_data import ScanInfo, load_scan, resolve_scan_dir  # noqa: E402
from trc_reader import read_trc  # noqa: E402

REDUCED_VERSION = 2          # reduced.npz の形式 (1: 旧簡易解析、2: matome 準拠)


def adjacent_pairs(channels) -> list[tuple[int, int]]:
    """番号が隣り合う ch の組 (ch 配列の添字)。例 C1..C4 → (0,1),(1,2),(2,3)。"""
    num = [int(c[1:]) for c in channels]
    return [(i, j) for i in range(len(num)) for j in range(len(num)) if num[j] == num[i] + 1]


def _prepass(scan: ScanInfo, cfg: FeatureConfig, n_sample: int = 24):
    """全体ノイズ基準 [mV] (ch ごと) と極性を、間引いた点から決める。"""
    step = max(1, scan.n_points // n_sample)
    noise = {ch: [] for ch in scan.channels}
    best = (0.0, -1.0)
    for idx in scan.index[::step]:
        for ch in scan.channels:
            p = scan.file_for(ch, int(idx))
            if not p.exists():
                continue
            try:
                w = read_trc(p)
            except Exception:
                continue
            v = w.volts()
            nb = max(int(round(cfg.baseline_frac * v.shape[1])), 4)
            noise[ch].append(v[:, :nb].std(axis=1, ddof=1) * 1e3)
            pol, size = detect_polarity(w, cfg)
            if size > best[0]:
                best = (size, pol)
    ref = np.array([float(np.median(np.concatenate(noise[ch]))) if noise[ch] else np.nan
                    for ch in scan.channels])
    return ref, best[1]


def _reduce_point(args):
    """1 測定点の全 ch を処理 (ProcessPool ワーカー)。"""
    scan_dir, prefix, channels, idx, cfg_dict, ref, pol, pairs = args
    cfg = FeatureConfig(**cfg_dict)
    feats, waves = [], []
    for ch in channels:
        path = Path(scan_dir) / f"{ch}--{prefix}--{idx:05d}.trc"
        ft, wave = None, None
        try:
            w = read_trc(path)
            ft = segment_features(w, cfg, pol)
            wave = w.volts().mean(axis=0).astype(np.float32)
        except FileNotFoundError:
            pass
        except Exception as exc:  # 壊れたファイルは飛ばして続行
            print(f"[warn] {path.name}: {exc}", flush=True)
        feats.append(ft)
        waves.append(wave)
    return idx, reduce_point(feats, ref, cfg, pol, pairs), waves


def reduce_scan(scan: ScanInfo, out_path: Path, cfg: FeatureConfig, workers: int = 8) -> Path:
    n, chs = scan.n_points, list(scan.channels)
    row_of = {int(v): i for i, v in enumerate(scan.index)}
    pairs = adjacent_pairs(chs)

    # 波形長は先頭の存在するファイルから決める
    nsamp, t_ns, dt_ns = 0, np.zeros(0), np.nan
    for idx in scan.index:
        p = scan.file_for(chs[0], int(idx))
        if p.exists():
            w0 = read_trc(p)
            nsamp = w0.n_samples
            if w0.n_segments < cfg.n_min:
                print(f"[warn] 1 ファイルのセグメント数が {w0.n_segments} しかありません (オシロ側で平均した波形?)。"
                      "MPV・ジッター・時間分解能・位置分解能は計算できず、振幅などは中央値で代用します", flush=True)
            dt_ns = w0.horiz_interval * 1e9
            t_ns = (w0.horiz_offset + w0.horiz_interval * np.arange(nsamp)) * 1e9
            break
    if nsamp == 0:
        raise FileNotFoundError(f"読める .trc がありません: {scan.scan_dir}")

    t0 = time.time()
    ref, pol = _prepass(scan, cfg)
    print(f"  全体ノイズ基準 [mV]: " + ", ".join(f"{c} {r:.2f}" for c, r in zip(chs, ref))
          + f"  極性 {'負' if pol < 0 else '正'}", flush=True)

    quant = {ch: np.full((n, len(QUANT)), np.nan) for ch in chs}
    waves = {ch: np.full((n, nsamp), np.nan, np.float32) for ch in chs}
    ev = np.full((n, len(EV_QUANT)), np.nan)
    pr = np.full((n, len(pairs), len(PAIR_QUANT)), np.nan)
    eta_hist = np.zeros((n, len(pairs), 2, ETA_NBIN), np.uint16)
    jit_hist = np.zeros((n, 2, JIT_NBIN), np.uint16)

    jobs = [(str(scan.scan_dir), scan.prefix, tuple(chs), int(idx), cfg.as_dict(), ref, pol, pairs)
            for idx in scan.index]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, (idx, res, wv) in enumerate(pool.map(_reduce_point, jobs, chunksize=4)):
            i = row_of[idx]
            for c, ch in enumerate(chs):
                quant[ch][i] = res["ch"][c]
                if wv[c] is not None and len(wv[c]) == nsamp:
                    waves[ch][i] = wv[c]
            ev[i], pr[i] = res["ev"], res["pair"]
            eta_hist[i], jit_hist[i] = res["eta_hist"], res["jit_lead_hist"]
            if (k + 1) % 100 == 0 or k + 1 == n:
                el = time.time() - t0
                print(f"  reduce {k + 1}/{n}  {el:.0f}s (ETA {el / (k + 1) * (n - k - 1):.0f}s)",
                      flush=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path, version=REDUCED_VERSION,
        index=scan.index, x_um=scan.x_um, y_um=scan.y_um, z_um=scan.z_um,
        tune=scan.tune, ok=scan.ok, xn=scan.xn, yn=scan.yn, zn=scan.zn,
        t_ns=t_ns, dt_ns=dt_ns, noise_ref_mV=ref, polarity=pol,
        quant_names=np.array(QUANT), ev_names=np.array(EV_QUANT), pair_quant_names=np.array(PAIR_QUANT),
        channels=np.array(chs),
        pair_names=np.array([chs[a] + chs[b] for a, b in pairs]),
        pair_index=np.array(pairs, int).reshape(-1, 2),
        ev=ev, pair=pr, eta_hist=eta_hist, jit_lead_hist=jit_hist,
        jit_edges_ps=np.linspace(0.0, JIT_MAX_PS, JIT_NBIN + 1),
        cfg_json=np.str_(json.dumps(cfg.as_dict())),
        meta_json=np.str_(scan.meta_json()),
        **{f"q_{ch}": quant[ch] for ch in chs},
        **{f"w_{ch}": waves[ch] for ch in chs})
    print(f"reduced → {out_path} ({out_path.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s)")
    return out_path


def load_reduced(scan: ScanInfo, results_root=None, force: bool = False,
                 cfg: FeatureConfig | None = None, workers: int = 8):
    """キャッシュ (reduced.npz) を読み込む。無い・形式が古い・設定が違う・force なら作り直す。"""
    cfg = cfg or FeatureConfig()
    out = scan.results_dir(results_root) / "reduced.npz"
    redo = force or not out.exists()
    if not redo:
        with np.load(out, allow_pickle=False) as z:
            if "version" not in z.files or int(z["version"]) != REDUCED_VERSION:
                print("reduced キャッシュの形式が古いので作り直します")
                redo = True
            elif json.loads(str(z["cfg_json"])) != cfg.as_dict():
                print("解析設定がキャッシュと違うので作り直します")
                redo = True
    if redo:
        reduce_scan(scan, out, cfg, workers=workers)
    else:
        print(f"reduced キャッシュ使用: {out} (再計算は --force)")
    return np.load(out, allow_pickle=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scan", help="スキャンフォルダのパスまたは名前")
    ap.add_argument("--log", default=None, help="スキャンログ CSV (省略時自動検索)")
    ap.add_argument("--results", default=None, help="結果ルート (既定 <ws>\\results)")
    ap.add_argument("--force", action="store_true", help="キャッシュを作り直す")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    scan = load_scan(resolve_scan_dir(args.scan), args.log)
    print(f"scan={scan.name} mode={scan.mode} points={scan.n_points} ch={scan.channels}")
    load_reduced(scan, args.results, force=args.force, workers=args.workers)


if __name__ == "__main__":
    main()
