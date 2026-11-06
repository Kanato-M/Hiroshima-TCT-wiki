# -*- coding: utf-8 -*-
"""2 ストリップの振幅比 η による位置再構成と位置分解能 (1dY / 2dYX / 1dYtune 共通)。

方法 (解析ノート matome §9 と同じ。元の実装は analysis/macro/matome/a4_position_resolution.py):
  η = A_B / (A_A + A_B)  (ギャップを挟むペア、振幅和 > 40 mV)。縮約で点ごとの η ヒストグラムを
  偶数番・奇数番イベント別に作ってある。
  1. 1 本の Y 走査 (2D なら列) × ギャップごとに、各点 (ステージ位置 = 真値) の η の平均から
     較正曲線 η(u) を作る (u = A 側ストリップ中心からの距離)。
  2. イベントごとの η を較正曲線の逆引き (単調部分の表、範囲外は端の傾きで外挿) で u に直す。
  3. 残差 u_rec − u_true の ±1.5σ コアガウス σ を位置分解能とする (スポット径とステージ再現性を含む)。
  主値は「偶数番で較正し奇数番で評価」(test)。全イベントの自己較正 (in-situ) も出す。
  ノイズからの期待値: σ_η = √(A_B²σ_A² + A_A²σ_B²)/(A_A+A_B)² を局所的な傾き |dη/du| で割る。
  実効ピッチ: 中央部 (0.2 < η < 0.8) の直線フィット η = a + u/P_eff。
"""
from __future__ import annotations

import numpy as np

import stats as st
from features import ETA_NBIN

ETA_C = (np.arange(ETA_NBIN) + 0.5) / ETA_NBIN
ETA_W = 1.0 / ETA_NBIN
N_MIN = 100                      # 較正・評価に要求する点あたりのイベント数 (偶奇それぞれ)
KEYS = ("sig_test", "sig_test_err", "bias_test", "sig_insitu", "sig_insitu_err", "sig_exp",
        "eta_mean", "eta_err", "deta_du")


def lut_from_rows(u, eta_mean):
    """(u, η 平均) の単調増加部分列を逆引き表にする。"""
    o = np.argsort(u)
    u, e = u[o], eta_mean[o]
    keep = [0]
    for i in range(1, e.size):
        if e[i] > e[keep[-1]]:
            keep.append(i)
    keep = np.array(keep)
    return u[keep], e[keep]


def invert(eta, lut_u, lut_e):
    """η → u。表の範囲外は端の区間の傾きで直線外挿する。"""
    u = np.interp(eta, lut_e, lut_u)
    lo, hi = eta < lut_e[0], eta > lut_e[-1]
    u[lo] = lut_u[0] + (eta[lo] - lut_e[0]) * (lut_u[1] - lut_u[0]) / (lut_e[1] - lut_e[0])
    u[hi] = lut_u[-1] + (eta[hi] - lut_e[-1]) * (lut_u[-1] - lut_u[-2]) / (lut_e[-1] - lut_e[-2])
    return u


def residuals(hist, lut_u, lut_e, u_true, rng):
    """η ヒストグラム → 残差 u_rec − u_true (ビン幅内で一様に散らす)。"""
    eta = np.repeat(ETA_C, np.asarray(hist).astype(int))
    eta = eta + (rng.random(eta.size) - 0.5) * ETA_W
    return invert(eta, lut_u, lut_e) - u_true


def calibrate_line(rows, u, eta_hist_g, amp_a, amp_b, noise_a, noise_b, out, rng):
    """1 本の Y 走査 × 1 ギャップ。rows: 点の番号、u: A 側中心からの距離、eta_hist_g: (npt, 2, NBIN)。

    out[KEYS] の rows の位置に結果を書き込む。返り値 dict(pitch_eff, pitch_eff_err, lut) または None。
    """
    hist = eta_hist_g[rows].astype(np.int64)
    good = (hist[:, 0].sum(axis=1) >= N_MIN) & (hist[:, 1].sum(axis=1) >= N_MIN)
    rows, u, hist = rows[good], u[good], hist[good]
    if rows.size < 6:
        return None
    o = np.argsort(u)
    rows, u, hist = rows[o], u[o], hist[o]
    h_even, h_odd = hist[:, 0], hist[:, 1]
    h_all = h_even + h_odd
    em_even = (h_even * ETA_C).sum(axis=1) / h_even.sum(axis=1)
    em_all = (h_all * ETA_C).sum(axis=1) / h_all.sum(axis=1)
    var = (h_all * (ETA_C[None, :] - em_all[:, None]) ** 2).sum(axis=1) / h_all.sum(axis=1)
    em_err = np.sqrt(var / h_all.sum(axis=1))
    out["eta_mean"][rows], out["eta_err"][rows] = em_all, em_err
    res = {"pitch_eff": np.nan, "pitch_eff_err": np.nan, "lut": None}
    cen = (em_all > 0.2) & (em_all < 0.8)
    if cen.sum() >= 4:
        a, ea, b, eb, chi2 = st.wlinfit(u[cen], em_all[cen], np.maximum(em_err[cen], 1e-5))
        if b > 0:
            res["pitch_eff"], res["pitch_eff_err"] = 1.0 / b, eb / b ** 2
    lu_e, le_e = lut_from_rows(u, em_even)
    lu_a, le_a = lut_from_rows(u, em_all)
    if lu_e.size < 4 or lu_a.size < 4:
        return res
    res["lut"] = (lu_a, le_a)
    slope = np.gradient(em_all, u)
    out["deta_du"][rows] = slope
    for i, r in enumerate(rows):
        s, mu, se = st.fit_gaussian_core(residuals(h_odd[i], lu_e, le_e, u[i], rng))
        out["sig_test"][r], out["sig_test_err"][r], out["bias_test"][r] = s, se, mu
        s, mu, se = st.fit_gaussian_core(residuals(h_all[i], lu_a, le_a, u[i], rng))
        out["sig_insitu"][r], out["sig_insitu_err"][r] = s, se
        aa, ab, na, nb = amp_a[r], amp_b[r], noise_a[r], noise_b[r]
        if np.isfinite(aa) and np.isfinite(ab) and slope[i] > 0:
            out["sig_exp"][r] = np.sqrt(ab ** 2 * na ** 2 + aa ** 2 * nb ** 2) / (aa + ab) ** 2 / slope[i]
    return res


def analyze(r, f, line_id=None, select=None):
    """全ギャップ × 全ライン (2D なら列、1D なら 1 本) を較正する。

    r: plot_common.Red、f: plot_common.fold の出力、line_id: (npt,) ラインの番号 (None なら全点 1 本)、
    select: 使う点のマスク。返り値 (out dict of (npt,), lines dict {(line, gap): {...}})。
    """
    npt = r.npt
    out = {k: np.full(npt, np.nan) for k in KEYS}
    line_id = np.zeros(npt, int) if line_id is None else np.asarray(line_id)
    sel = r.ok if select is None else (select & r.ok)
    amp, noise = r.q("amp_mpv_mV"), r.q("noise_med_mV")
    rng = np.random.default_rng(20261003)
    lines = {}
    for g, (ca, cb) in enumerate(r.pairs):
        eh = r.eta_hist[:, g]
        for ln in np.unique(line_id[sel]):
            rows = np.where(sel & (line_id == ln) & (f["gap"] == g))[0]
            if rows.size < 6:
                continue
            res = calibrate_line(rows, f["u"][rows], eh, amp[:, ca], amp[:, cb],
                                 noise[:, ca], noise[:, cb], out, rng)
            if res is not None:
                lines[(int(ln), g)] = res
    return out, lines
