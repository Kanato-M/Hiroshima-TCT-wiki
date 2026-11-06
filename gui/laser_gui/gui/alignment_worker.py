# -*- coding: utf-8 -*-
"""信号ベースの自動センサーアライメント (原点出し + 傾き係数校正)。

カメラではなくレーザー誘起信号そのものを位置センサーとして使う:
金属電極上では信号がゼロになり、エッジはビーム半径 (数 µm) で切れる
ため、振幅プロファイルのディップ/エッジが µm 精度の物差しになる。
振幅の読みはオートフォーカスと同じ PAVA (NORM モード、波形保存なし)。

前提 (開始時の状態):
  - レーザー ON (または自動 ON 指定)、トリガー EXT で波形が出ている
  - ビームが C1 ストリップ上、読み出し側端 (X=0) から 50〜200 µm
    内側にある (カメラ/プリセットの粗寄せで十分。±100 µm 程度の誤差可)

★フォーカスの前提 (2026-09-07 実測で確定)★
  フォーカスが合うほど振幅は「小さく」なる: 局所キャリア密度増による
  LGAD ゲイン抑制 (プラズマ効果)。カメラでフォーカスが最良のとき振幅は
  最小だった。旧「振幅最大 = 焦点」方式は誤り。
  Z 掃引は**信号が明瞭に見えるストリップ横のシリコン上**で行う
  (電極は幅 ~50 µm あり、直上ではスポットがデフォーカスしても金属内に
  収まったままで谷のコントラストが出ない — 2026-09-07 実機確認)。
  谷は高原のない V 字で左右非対称になり得る (2026-10-05 実機データ) ため、
  精密モードではローレンツ型 + 一次基線のフィット曲線の最小点を焦点とする
  (同一箇所の 2 回測定で 0.1 µm 以内に一致。旧 50% 閾値法は 0.4〜0.7 µm)。

モード "origin" (原点出し。粗検出のみ 約 2〜3 分、精密仕上げ+検証 約 7〜12 分):
  1. Y スキャン (粗) → 電極ディップから C1 電極中心の当たりを付ける
  2. 電極から probe_off (既定 80 µm) 横のシリコン上で Z 掃引 →
     振幅最小の Z (= 焦点) へ
  3. Y スキャン (精密, フォーカス後) → 電極中心 Y を確定
     ★精密仕上げ★ 1 パルス (2 µm) 刻み・多数回読み (中央値) で両エッジを
     erf フィット → 電極中心をサブ µm で決める。複数回の掃引で再現性、
     逆方向掃引でバックラッシュを推定する
  4. X スキャン (+側=パッド領域へ) → 信号消失エッジ = ストリップ露出部の
     読み出し側端 (②/③境界) を検出 ★精密仕上げ★ 同様に erf フィット
  5. シリコン上で Z 再確認 (精密: ローレンツ型フィット) →
     **エッジ + 632 µm (設計オフセット ⓪+①+② = 外枠 GND エッジ相当) へ
     全軸を負側から進入して論理原点リセット (R:W)** → ここが (0,0,0)。
     原点から X=−10 mm 移動するとギャップ④中央 (ストリップ領域の境界)
     に来る (scan_geometry の X 定義参照)
  6. ★検証★ R:W 後に電極中心 Y・信号エッジ X・焦点 Z を精密に再測定し、
     残差が 1 パルス (2 µm) 以上の軸があれば補正して R:W をやり直す
     (最大 verify_max_iter 回)。最終残差を結果に残す

モード "full" (フル校正。粗 約 8〜12 分、精密 約 15〜25 分) = origin の後に:
  7. X = x_points (標準センサーは -5300, -9900 µm) で Y ディップ中心 →
     シリコン上フォーカス Z → dY/dX (回転)・dZ/dX (チルト) を直線フィット
  8. Y = y_points (標準センサーは -500, -1500 µm = C2, C4) で同様に
     電極中心 + フォーカス Z → dZ/dY を直線フィット、ストリップピッチも確認
     (精密モードでは各測定点も 1 パルス刻みのフィットで決める)
  9. 係数を self.calibration に入れて原点 (0,0,0) へ戻る。保存は GUI
     スレッド側 (ステージパネル) が現在のセンサープロファイルへ行う

精度に関する実機知見 (2026-10-05 ログ解析):
  - 電極エッジは 1 パルス以内で急峻に落ち (erf σ ≈ 0.5〜2 µm)、直前に
    振幅が 10〜20 % 高くなる「エッジ増強」がある → エッジフィットは
    高原に一次の勾配項を持たせて吸収する
  - 閾値法と erf フィットの中心は 0.3 µm 以内で一致する。つまり信号
    ベースの測定自体は 1 µm 以下で自己整合しており、手動 (カメラ) 原点
    との数 µm の差は基準の定義差 (十字線とスポットのずれ、パッド端→外枠
    端の設計値、進入方向=バックラッシュ) に由来する可能性が高い
  - 3 µm ステップは 2 µm/pulse の格子に丸まり、指令値を記録すると最大
    1 µm の鋸歯状誤差が乗る → 実際のパルス位置を記録する

センサー形状 (外枠エッジ→露出開始のオフセット、測定点、プローブ位置、
ピッチ) は params で受け取る (sensors.py のプロファイル由来)。ボードや
センサーを変えてもこのファイルの編集は不要。

ピクセル型 (params["sensor_type"] == "pixel"、2026-10-16 追加) の原点出し:
  ボンディングパッド (無信号の正方形、辺 pad_um) が物差しになる。
  1. Y 掃引 (パッドを横切る) → ディップ中心 = パッド中心 Y の当たり
  2. パッド横のシリコン上 (パッド中心から y_probe_off 下、ピクセル内) で Z (振幅最小)
  3. Y 精密ディップ → パッド中心 Y
  4. X 掃引 (パッド中心の行) → ディップ中心 = パッド中心 X (精密は erf フィット)
  5. Z 再確認 → **パッドの +X/+Y 側の角 (中心 + pad/2, + pad/2) で R:W**。
     ピクセル中心 = パッド中心 = (−pad/2, −pad/2)、隣のピクセルは ±pitch
  6. 検証: X = −pad/2 の列で Y ディップ、その行で X ディップ、Z を再測定
  フル較正 (params["pads"] = [[CH, x, y], ...] = 他の読み出しピクセルのパッド中心):
  各パッドをその CH で Y → X → Z と測り、平面フィットで dZ/dX・dZ/dY、
  基準と同じ行のパッドから dY/dX、パッド間隔からピッチを確認する
  (レバーアームは 1 ピッチなので係数の精度はストリップより粗い)。

中断・エラー時: R:W 前なら開始位置へ復帰、R:W 後なら原点 (0,0,0) へ。
"""

from __future__ import annotations

import csv
import datetime
import math
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from .scan_worker import frange


# ----------------------------------------------------------------------
# プロファイル解析 (純関数: 単体テスト可能)
# ----------------------------------------------------------------------
def _smooth3(a: np.ndarray) -> np.ndarray:
    """端点を複製パディングした 3 点移動平均。

    ★np.convolve(mode='same') は端点が 2/3 に減衰し、浅い谷では端点が
    偽の「閾値以下」になって範囲端の誤判定を起こすため使わない★
    """
    a = np.asarray(a, float)
    if len(a) < 3:
        return a.copy()
    pad = np.concatenate([a[:1], a, a[-1:]])
    return (pad[:-2] + pad[1:-1] + pad[2:]) / 3.0



def dip_center(pos: np.ndarray, amp: np.ndarray,
               min_width: float = 4.0, max_width: float = 200.0,
               max_ratio: float = 0.5, min_depth: float = 0.0):
    """振幅プロファイルのディップ (谷) 中心を返す (50% 閾値法, 粗検出用)。

    高原と谷の 50% 閾値で両エッジを一次補間し、中点をディップ中心と
    する。Y の電極ディップ (金属上でほぼゼロ) と、Z フォーカスの
    ゲイン抑制谷 (浅い) の両方に使う。
    max_ratio: ディップと認める最大の谷/高原比。電極検出は 0.5、
      フォーカス前の粗検出は 0.75 (デフォーカスで浅くなる)、
      Z フォーカス谷は 0.9 (ゲイン抑制は数十 % の浅い谷) を使う。
    min_depth: 谷の最小深さ [V] (ノイズをディップと誤認しないため)。
    戻り値: (center, width, lo_edge, hi_edge)。見つからなければ ValueError。
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    if len(pos) < 7:
        raise ValueError("点数が少なすぎます")
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    # 3点移動平均でノイズをならす (端点は複製パディング)
    sm = _smooth3(amp)
    hi = float(np.percentile(sm, 85))
    lo = float(sm.min())
    if hi < 0.005:
        raise ValueError(
            f"信号がありません (plateau={hi * 1e3:.1f} mV)。"
            "ビーム位置・フォーカス・レーザー ON を確認してください")
    if lo > max_ratio * hi or hi <= 0:
        raise ValueError(
            f"明確なディップがありません (min={lo * 1e3:.1f} mV, "
            f"plateau={hi * 1e3:.1f} mV)。範囲内に電極が無いか、"
            "フォーカス不良の可能性")
    if hi - lo < min_depth:
        raise ValueError(
            f"谷が浅すぎます (深さ {(hi - lo) * 1e3:.1f} mV < "
            f"{min_depth * 1e3:.1f} mV)。ノイズの可能性")
    thr = 0.5 * (hi + lo)
    imin = int(np.argmin(sm))
    below = sm < thr
    if not below[imin]:
        raise ValueError("ディップ検出の閾値計算に失敗")
    # ディップ左エッジ (下降交差)
    i = imin
    while i > 0 and below[i - 1]:
        i -= 1
    if i == 0:
        raise ValueError("ディップが範囲の左端にかかっています。"
                         "範囲を広げるか中心をずらしてください")
    f = (sm[i - 1] - thr) / (sm[i - 1] - sm[i])
    e1 = pos[i - 1] + f * (pos[i] - pos[i - 1])
    # 右エッジ (上昇交差)
    j = imin
    while j < len(sm) - 1 and below[j + 1]:
        j += 1
    if j == len(sm) - 1:
        raise ValueError("ディップが範囲の右端にかかっています。"
                         "範囲を広げるか中心をずらしてください")
    f = (thr - sm[j]) / (sm[j + 1] - sm[j])
    e2 = pos[j] + f * (pos[j + 1] - pos[j])
    width = e2 - e1
    if not (min_width <= width <= max_width):
        raise ValueError(f"ディップ幅 {width:.1f} µm が電極として"
                         f"不自然です ({min_width}〜{max_width} µm 想定)")
    return 0.5 * (e1 + e2), width, e1, e2


def edge_position(pos: np.ndarray, amp: np.ndarray,
                  falling: bool = True):
    """信号ステップエッジ (50% 交差) の位置を返す (粗検出用)。

    falling=True: pos の増加方向で信号が高→低に落ちるエッジ (X 原点用:
    スキャン領域 → パッド領域)。戻り値: (edge_pos, hi, lo)。
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    if len(pos) < 7:
        raise ValueError("点数が少なすぎます")
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    sm = _smooth3(amp)
    hi = float(np.percentile(sm, 90))
    lo = float(np.percentile(sm, 10))
    if hi < 0.005:
        raise ValueError(
            f"信号がありません (hi={hi * 1e3:.1f} mV)。"
            "ビーム位置・フォーカス・レーザー ON を確認してください")
    if hi <= 0 or (hi - lo) < 0.5 * hi:
        raise ValueError(
            f"明確なエッジがありません (hi={hi * 1e3:.1f}, "
            f"lo={lo * 1e3:.1f} mV)。範囲内に境界が無い可能性")
    thr = 0.5 * (hi + lo)
    above = sm >= thr
    crossings = []
    for i in range(1, len(sm)):
        if falling and above[i - 1] and not above[i]:
            f = (sm[i - 1] - thr) / (sm[i - 1] - sm[i])
            crossings.append((abs(sm[i - 1] - sm[i]),
                              pos[i - 1] + f * (pos[i] - pos[i - 1])))
        elif not falling and not above[i - 1] and above[i]:
            f = (thr - sm[i - 1]) / (sm[i] - sm[i - 1])
            crossings.append((abs(sm[i] - sm[i - 1]),
                              pos[i - 1] + f * (pos[i] - pos[i - 1])))
    if not crossings:
        raise ValueError("エッジ交差が見つかりません。"
                         "掃引範囲を広げてください")
    # 最も急峻な交差を採用 (ノイズ交差の排除)
    return max(crossings)[1], hi, lo


def refine_extremum(zs: np.ndarray, amps: np.ndarray,
                    minimum: bool = False) -> float:
    """振幅カーブの極値位置 (極値点 ±1 の放物線補間)。

    minimum=True で最小 (フォーカス = 電極上の振幅最小、V 字の底)。
    """
    zs = np.asarray(zs, float)
    amps = np.asarray(amps, float)
    i = int(np.argmin(amps) if minimum else np.argmax(amps))
    if 0 < i < len(zs) - 1:
        y0, y1, y2 = amps[i - 1], amps[i], amps[i + 1]
        denom = y0 - 2 * y1 + y2
        if (denom > 0) if minimum else (denom < 0):
            return float(zs[i] + 0.5 * (y0 - y2) / denom
                         * (zs[i + 1] - zs[i]))
    return float(zs[i])


# ----------------------------------------------------------------------
# 精密フィット (numpy のみ。scipy は制御 PC に入っていないため使わない)
# ----------------------------------------------------------------------
_EDGE_MARKER = "端にかかって"     # 範囲端エラーの識別子 (再センタリング用)


def _erf(x):
    """ベクトル化 erf (Abramowitz–Stegun 7.1.26, |誤差| < 1.5e-7)。"""
    x = np.asarray(x, float)
    s = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-a * a)
    return s * y


def _phi(x):
    """標準正規分布の累積分布 Φ(x)。"""
    return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)))


def _batched_lstsq(cols: np.ndarray, y: np.ndarray):
    """cols: (K, n, p) の計画行列の束、y: (n,) → 係数 (K, p) と SSE (K,)。

    正規方程式を K 個まとめて解く (格子探索の内側で高速)。
    """
    At = np.transpose(cols, (0, 2, 1))
    ata = At @ cols
    aty = At @ y
    p = cols.shape[2]
    ridge = 1e-9 * np.trace(ata, axis1=1, axis2=2)[:, None, None] / p
    coef = np.linalg.solve(ata + ridge * np.eye(p), aty[..., None])[..., 0]
    resid = y[None, :] - np.einsum("knp,kp->kn", cols, coef)
    return coef, np.sum(resid * resid, axis=1)


def _grid_min(grid: np.ndarray, sse: np.ndarray, n_pts: int, n_free: int):
    """SSE 格子の最小点をサブ格子で返す: (位置, 統計誤差 1σ, 最小 SSE)。

    σ→0 (遷移がサンプル間に落ちた) で SSE が格子上で平坦になる場合は
    平坦区間の中点を返す (= 2 サンプルの中点が最良推定。argmin だと
    区間の端に偏る)。平坦でなければ放物線補間し、SSE の曲率から誤差
    var ≈ 2·s² / SSE'' を見積もる。
    """
    i = int(np.argmin(sse))
    smin = float(sse[i])
    tol = smin * 1e-4 + 1e-15
    lo = i
    while lo > 0 and sse[lo - 1] <= smin + tol:
        lo -= 1
    hi = i
    while hi < len(sse) - 1 and sse[hi + 1] <= smin + tol:
        hi += 1
    h = float(grid[1] - grid[0]) if len(grid) > 1 else 0.1
    flat = (hi - lo) * h
    if flat > 3.0 * h:
        return 0.5 * float(grid[lo] + grid[hi]), flat / math.sqrt(12.0), smin
    x = float(grid[i])
    err = float("nan")
    if 0 < i < len(grid) - 1:
        y0, y1, y2 = sse[i - 1], sse[i], sse[i + 1]
        d2 = y0 - 2 * y1 + y2
        if d2 > 0:
            x = float(grid[i] + 0.5 * (y0 - y2) / d2 * h)
            s2 = smin / max(n_pts - n_free, 1)
            err = math.sqrt(2.0 * s2 / (d2 / (h * h)))
    return x, err, smin


def fit_edge(pos, amp, falling: bool = True, sigma_max: float = 10.0) -> dict:
    """1 本のステップエッジを erf モデルで最小二乗フィットする。

    モデル: s(x) = lo + (a + b·(x − x0)) · Φ(∓(x − x0)/σ)
      falling=True: +x 方向で高→低。a, b はエッジ位置での高原レベルと
      その勾配 (電荷共有の勾配・エッジ直前の振幅増強を一次で吸収)。
    x0 (0.1 µm 刻み) と σ (等比列) を格子探索し (各格子点で lo, a, b は
    線形最小二乗)、x0 をサブ格子に補正する。
    戻り値 dict: center, sigma, hi (x0 での高原レベル), lo, slope,
      rms [V], err (x0 の統計誤差 1σ [µm]), n
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    if len(pos) < 6:
        raise ValueError("点数が少なすぎます")
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    hi_p = float(np.percentile(amp, 90))
    lo_p = float(np.percentile(amp, 10))
    if hi_p < 0.005:
        raise ValueError(f"信号がありません (hi={hi_p * 1e3:.1f} mV)")
    if hi_p - lo_p < 0.5 * hi_p:
        raise ValueError(f"明確なエッジがありません (hi={hi_p * 1e3:.1f}, "
                         f"lo={lo_p * 1e3:.1f} mV)")
    step = float(np.median(np.diff(pos)))
    sgn = -1.0 if falling else 1.0
    x0_grid = np.arange(pos[0] + 0.5 * step, pos[-1] - 0.5 * step + 1e-9,
                        0.1)
    sig_grid = np.geomspace(0.5, sigma_max, 14)
    best = None
    for sig in sig_grid:
        dx = pos[None, :] - x0_grid[:, None]
        ph = _phi(sgn * dx / sig)
        cols = np.stack([np.ones_like(ph), ph, dx * ph], axis=2)
        coef, sse = _batched_lstsq(cols, amp)
        i = int(np.argmin(sse))
        if best is None or sse[i] < best[0]:
            best = (float(sse[i]), float(sig), coef[i].copy(), sse)
    sse_min, sig, coef, sse_arr = best
    x0, err, _ = _grid_min(x0_grid, sse_arr, len(pos), 5)
    lo, a, b = (float(v) for v in coef)
    if x0 < pos[0] + step or x0 > pos[-1] - step:
        raise ValueError(f"エッジが範囲の{_EDGE_MARKER}います。"
                         "範囲を広げるか中心をずらしてください")
    if sig >= sig_grid[-1] * 0.999:
        raise ValueError(f"エッジが鈍すぎます (σ≈{sig:.1f} µm)。"
                         "フォーカス不良の可能性")
    if a <= 0:
        raise ValueError("エッジフィットが不正 (高原レベル ≤ 0)")
    return {"center": float(x0), "sigma": float(sig), "hi": a, "lo": lo,
            "slope": b, "rms": math.sqrt(sse_min / len(pos)), "err": err,
            "n": int(len(pos))}


def fit_dip(pos, amp, coarse=None, min_width: float = 4.0,
            max_width: float = 200.0, sigma_max: float = 10.0) -> dict:
    """ディップ (谷) の左右エッジを個別に erf フィットして中心を返す。

    coarse: (左エッジ, 右エッジ) の粗い推定 (無ければ dip_center の
    50% 閾値法で求める)。粗中心より左を falling、右を rising として
    別々にフィットする (左右で高原レベルが違っても偏らない)。
    戻り値 dict: center, width, err, left, right (fit_edge の dict)
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    if coarse is None:
        _, _, e1, e2 = dip_center(pos, amp, min_width, max_width,
                                  max_ratio=0.75)
    else:
        e1, e2 = coarse
    mid = 0.5 * (e1 + e2)
    lm = pos <= mid
    rm = pos >= mid
    left = fit_edge(pos[lm], amp[lm], falling=True, sigma_max=sigma_max)
    right = fit_edge(pos[rm], amp[rm], falling=False, sigma_max=sigma_max)
    width = right["center"] - left["center"]
    if not (min_width <= width <= max_width):
        raise ValueError(f"ディップ幅 {width:.1f} µm が電極として"
                         f"不自然です ({min_width}〜{max_width} µm 想定)")
    errs = [left["err"], right["err"]]
    err = 0.5 * math.hypot(*errs) if all(np.isfinite(errs)) else float("nan")
    return {"center": 0.5 * (left["center"] + right["center"]),
            "width": width, "err": err, "left": left, "right": right}


def fit_focus(zs, amps, min_depth: float = 0.004,
              gamma_range=(2.0, 40.0)) -> dict:
    """Z 掃引の谷 (ゲイン抑制) をローレンツ型 + 一次基線でフィットする。

    A(z) = a + b·(z − z̄) − c / (1 + ((z − z0)/γ)²)
    (ガウスビーム焦点近傍のピーク強度 ∝ 1/(1+(z/zR)²) に対応。一次項 b
    は谷の左右非対称 — 実機で観測 — を吸収する)。z0 (0.1 µm 刻み)・
    γ (等比列) を格子探索し、各格子点で a, b, c は線形最小二乗。
    **焦点 (center) はフィット曲線そのものの最小点** とする (2026-10-05
    実機データで同一箇所の 2 回測定が 0.1 µm 以内に一致。ローレンツ中心
    z0 や 50% 閾値法は 0.4〜0.7 µm ばらついた)。
    戻り値 dict: center, z0, gamma, depth [V], depth_ratio, base, slope,
      rms, err (z0 の統計誤差), n
    """
    zs = np.asarray(zs, float)
    amps = np.asarray(amps, float)
    if len(zs) < 7:
        raise ValueError("点数が少なすぎます")
    order = np.argsort(zs)
    zs, amps = zs[order], amps[order]
    step = float(np.median(np.diff(zs)))
    zbar = float(zs.mean())
    z0_grid = np.arange(zs[0] + 0.5 * step, zs[-1] - 0.5 * step + 1e-9, 0.1)
    g_grid = np.geomspace(gamma_range[0], gamma_range[1], 14)
    best = None
    for g in g_grid:
        lor = 1.0 / (1.0 + ((zs[None, :] - z0_grid[:, None]) / g) ** 2)
        cols = np.stack([np.ones_like(lor),
                         np.broadcast_to(zs - zbar, lor.shape), -lor], axis=2)
        coef, sse = _batched_lstsq(cols, amps)
        sse = np.where(coef[:, 2] > 0, sse, np.inf)   # 谷 (c > 0) のみ
        i = int(np.argmin(sse))
        if np.isfinite(sse[i]) and (best is None or sse[i] < best[0]):
            best = (float(sse[i]), float(g), coef[i].copy(), sse)
    if best is None:
        raise ValueError("谷が見つかりません (振幅が単調)")
    sse_min, g, coef, sse_arr = best
    z0, err, _ = _grid_min(z0_grid, sse_arr, len(zs), 5)
    a, b, c = (float(v) for v in coef)
    if c < min_depth:
        raise ValueError(f"谷が浅すぎます (深さ {c * 1e3:.1f} mV < "
                         f"{min_depth * 1e3:.1f} mV)。ノイズの可能性")
    # フィット曲線の最小点 (0.05 µm 格子)
    zz = np.arange(zs[0], zs[-1] + 1e-9, 0.05)
    curve = a + b * (zz - zbar) - c / (1.0 + ((zz - z0) / g) ** 2)
    center = float(zz[int(np.argmin(curve))])
    if center < zs[0] + step or center > zs[-1] - step:
        raise ValueError(f"谷が範囲の{_EDGE_MARKER}います。"
                         "範囲を広げるか中心をずらしてください")
    return {"center": center, "z0": float(z0), "gamma": g, "depth": c,
            "depth_ratio": c / a if a else float("nan"),
            "base": a, "slope": b, "rms": math.sqrt(sse_min / len(zs)),
            "err": err, "n": int(len(zs))}


def _r(v, nd=2):
    """結果 dict 用: float に丸める (NaN はそのまま)。"""
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return v


# ----------------------------------------------------------------------
class AlignmentWorker(QThread):
    """自動アライメント実行スレッド。

    params:
      mode: "origin" | "full"
      ch: 振幅を読むオシロ CH ("C1".."C4")
      af_range_um / af_step_um: オートフォーカス掃引 (既定 ±20 / 2)
      y_half_um / y_step_um:    Y ディップ掃引 (既定 ±150 / 3)
      x_back_um / x_fwd_um / x_step_um: X エッジ掃引 (既定 -30/+250 / 4)
      x_probe_off_um: X エッジ掃引の Y オフセット (電極中心から、既定 50。
        80 だとボンドパッドの Y 幅から外れて信号が消えずエッジが出ない)
      y_probe_off_um: Z フォーカスの Y オフセット (既定 80)
      x_frame_offset_um: 信号エッジ (②/③境界) → 外枠エッジの設計オフセット
      pitch_um:  公称ストリップピッチ (実測ピッチの同定用)
      x_points:  フル校正の追加 X 測定点 (既定 [-5300, -9900])
      y_points:  フル校正の追加 Y 測定点 [[y, 読み出し CH], ...]
                 (既定 [[-500, "C2"], [-1500, "C4"]])
      coef0:     現在の係数 (dzdx, dzdy, dydx, 出典)。測定点の予測と、
                 プローブ位置 (電極横) で測った焦点 Z を電極中心上へ
                 換算する微小補正 (標準センサーで 0.3 µm) に使う
      settle_s / reads / backlash_um / move_timeout_s
      laser_on:  開始時にレーザーを自動 ON する
      reset_origin: 原点出し後に R:W する (既定 True)
      pre_move_xy: 開始前に移動する論理座標 (x, y)。原点出し済みの状態から
                 やり直すとき、基準ストリップ上の開始位置へ自動で寄せる
      --- 精密仕上げ (2026-10 追加) ---
      fine:          True で粗検出の後に 1 パルス刻みの精密掃引+フィット
      fine_reads:    精密掃引の 1 点あたり読み取り回数 (既定 4, 中央値)
      fine_settle_s: 精密掃引の整定時間 (既定 0.25)
      fine_passes:   原点出しの精密掃引の繰り返し回数 (既定 2。差を
                     再現性として報告)
      fine_margin_um: Y 精密掃引で電極エッジの外側に取る余裕 (既定 16)
      fine_x_half_um: X 精密掃引の半幅 (既定 20)
      backlash_check: 逆方向掃引を 1 回加えてバックラッシュを推定 (既定 True)
      verify:        R:W 後に原点を再測定し、1 パルス以上の残差を補正 (既定 True)
      verify_max_iter: 補正の最大回数 (既定 2)
      csv_path:      掃引プロファイルの記録先 (省略可)

    終了後に GUI スレッドが読む結果:
      origin_done: R:W まで完了したか
      calibration: フル校正の結果 dict (origin のみ / 失敗時は None)
      origin_report: 原点出しの精密仕上げ・検証結果 dict (粗モードは None)
    """

    progress = Signal(int, int, str)       # (完了点数, 総点数, メッセージ)
    finished_align = Signal(bool, str)     # (成功か, メッセージ)

    def __init__(self, scope, stage, laser, params: dict, parent=None):
        super().__init__(parent)
        self.scope = scope
        self.stage = stage
        self.laser = laser
        self.p = dict(params)
        self._abort = False
        self._origin_done = False
        self.origin_done = False        # GUI から読む公開フラグ
        self.calibration: dict | None = None
        self.origin_report: dict | None = None
        self._laser_turned_on = False
        self._warnings: list[str] = []
        self._csv_rows: list[tuple] = []
        # 現在の振幅読み出しチャンネル。フル校正の Y 測定点では
        # そのストリップを読むチャンネルに切り替わる (下記)
        self._ch = self.p.get("ch", "C1")
        self._last_focus_exact = float("nan")
        self._last_focus_info: dict = {}
        dzdx0, dzdy0, _, _ = self.p.get("coef0", (0.0, 0.0, 0.0, ""))
        self._dzdx0 = float(dzdx0)
        self._dzdy0 = float(dzdy0)

    def abort(self):
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        self.scope.mirror_paused = True
        try:
            self._run()
        except _Aborted:
            self._restore()
            self.finished_align.emit(False, "アライメントを中断しました")
        except Exception as exc:
            self._restore()
            self.finished_align.emit(False, f"アライメントエラー: {exc}")
        finally:
            self.scope.mirror_paused = False
            self._write_csv()
            # 自動 ON したレーザーは安全のため OFF に戻す (スキャン時と同方針)
            if self._laser_turned_on and self.p.get("laser_off", False):
                try:
                    self.laser.set_emission(False)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 低レベルヘルパ
    # ------------------------------------------------------------------
    def _check_abort(self):
        if self._abort:
            raise _Aborted()

    def _goto(self, x, y, z):
        self._check_abort()
        self.stage.move_to_um_relative(x, y, z)
        self.stage.wait_ready(timeout=self.p.get("move_timeout_s", 60),
                              abort_check=lambda: self._abort)
        self._check_abort()

    def _quant(self, axis: int, um: float) -> float:
        """µm をステージのパルス格子 (2 µm/pulse) に丸める。"""
        return float(self.stage.pulse_to_um(axis,
                                            self.stage.um_to_pulse(axis, um)))

    def _pulse_um(self, axis: int) -> float:
        """1 パルスの移動量 [µm] (精密掃引の刻み)。"""
        return abs(float(self.stage.pulse_to_um(axis, 1))) or 2.0

    def _amp(self, reads: int | None = None) -> float:
        """PAVA 振幅の複数回読み [V]。3 回以上なら中央値 (外れ値に強い)。

        チャンネルは self._ch (フル校正の Y 測定点では対象ストリップの
        チャンネルに切り替わる — 固定チャンネルだと隣のストリップ上で
        信号がほぼ無く検出に失敗する。2026-09-07 実機フィードバック)。
        """
        n = int(reads or self.p.get("reads", 2))
        vals = []
        for _ in range(n):
            v, state = self.scope.measure_pava(self._ch,
                                               self.p.get("pava", "PKPK"))
            if not math.isnan(v) and state.upper() not in ("NP", "IV"):
                vals.append(v)
            time.sleep(0.05)
        if not vals:
            raise ValueError(
                f"{self._ch} の振幅が読めません (波形なし?)。"
                "レーザー ON・トリガー動作中か確認してください")
        if len(vals) >= 3:
            return float(np.median(vals))
        return sum(vals) / len(vals)

    def _sweep(self, axis: int, base: list[float], offsets: list[float],
               label: str, reads: int | None = None,
               settle: float | None = None, descending: bool = False,
               tag: str = "") -> tuple[np.ndarray, np.ndarray]:
        """1軸を offsets に沿って掃引し (位置, 振幅) を返す。

        バックラッシュ対策: 最小側からさらに backlash_um 手前に一度
        入ってから一方向 (昇順) で掃引する。descending=True は逆
        (最大側の先から降順。バックラッシュ推定用)。
        記録する位置は指令値ではなく**実際のパルス格子に丸めた値**
        (3 µm ステップは 2 µm 格子に丸まり、指令値のままだと最大 1 µm の
        鋸歯状誤差が乗る — 2026-10-05 ログで確認)。
        """
        backlash = self.p.get("backlash_um", 10.0)
        if settle is None:
            settle = self.p.get("settle_s", 0.15)
        offsets = sorted(offsets)
        if descending:
            offsets = offsets[::-1]
        lead = list(base)
        lead[axis] = base[axis] + offsets[0] + (backlash if descending
                                                else -backlash)
        self._goto(*lead)
        positions, amps = [], []
        n = len(offsets)
        for i, off in enumerate(offsets):
            tgt = list(base)
            tgt[axis] = self._quant(axis, base[axis] + off)
            self._goto(*tgt)
            time.sleep(settle)
            a = self._amp(reads)
            positions.append(tgt[axis])
            amps.append(a)
            if tag:
                self._csv_rows.append((tag, "XYZ"[axis], i, self._ch,
                                       tgt[0], tgt[1], tgt[2], a))
            self.progress.emit(i + 1, n,
                               f"{label} {i + 1}/{n}: "
                               f"{'XYZ'[axis]}={tgt[axis]:+.1f} µm "
                               f"{a * 1e3:.1f} mV")
        return np.array(positions), np.array(amps)

    def _approach(self, target: list[float]):
        """全軸を負側 (掃引と同じ向き) から目標へ進入する (バックラッシュ対策)。"""
        b = self.p.get("backlash_um", 10.0)
        self._goto(target[0] - b, target[1] - b, target[2] - b)
        self._goto(*target)

    def _fine_cfg(self) -> dict:
        p = self.p
        return {"reads": int(p.get("fine_reads", 4)),
                "settle": float(p.get("fine_settle_s", 0.25)),
                "passes": max(1, int(p.get("fine_passes", 2))),
                "margin": float(p.get("fine_margin_um", 16.0)),
                "x_half": float(p.get("fine_x_half_um", 20.0))}

    # ------------------------------------------------------------------
    # 精密測定 (1 パルス刻み・多数回読み・モデルフィット)
    # ------------------------------------------------------------------
    def _fine_dip(self, base: list[float], e1: float, e2: float,
                  label: str, passes: int, backlash_check: bool = False,
                  tag: str = "", axis: int = 1,
                  max_width: float = 200.0) -> dict:
        """電極ディップの精密測定。e1/e2 は粗エッジ (絶対 Y)。

        [e1−margin, e2+margin] を 1 パルス刻みで passes 回昇順掃引し、
        各回を fit_dip → 中心の平均と回間の差 (再現性) を返す。
        backlash_check なら降順掃引を 1 回加え、昇順との中心差を
        バックラッシュ推定として返す (原点は昇順進入で決めるので
        昇順の値を採用)。
        """
        cfg = self._fine_cfg()
        step = self._pulse_um(axis)
        offsets = [v - base[axis] for v in frange(e1 - cfg["margin"],
                                                  e2 + cfg["margin"], step)]
        centers, widths, errs, sigmas = [], [], [], []
        for k in range(passes):
            ys, amps = self._sweep(
                axis, base, offsets, f"{label} 精密 {k + 1}/{passes}",
                reads=cfg["reads"], settle=cfg["settle"],
                tag=f"{tag}:asc{k + 1}")
            f = fit_dip(ys, amps, coarse=(e1, e2), max_width=max_width)
            centers.append(f["center"])
            widths.append(f["width"])
            errs.append(f["err"])
            sigmas.append(0.5 * (f["left"]["sigma"] + f["right"]["sigma"]))
        res = {"center": float(np.mean(centers)),
               "centers": [_r(c) for c in centers],
               "spread": float(max(centers) - min(centers)),
               "width": float(np.mean(widths)),
               "err": float(np.nanmean(errs)) if errs else float("nan"),
               "sigma": float(np.mean(sigmas)),
               "n_pass": passes, "n_points": len(offsets),
               "backlash": float("nan")}
        if backlash_check:
            ys, amps = self._sweep(
                axis, base, offsets, f"{label} 逆方向 (バックラッシュ推定)",
                reads=cfg["reads"], settle=cfg["settle"], descending=True,
                tag=f"{tag}:desc")
            try:
                fd = fit_dip(ys, amps, coarse=(e1, e2), max_width=max_width)
                res["center_desc"] = fd["center"]
                res["backlash"] = fd["center"] - res["center"]
            except ValueError as exc:
                self._warnings.append(f"{label}: 逆方向掃引のフィット不可 "
                                      f"({exc})")
        return res

    def _fine_edge(self, base: list[float], x_edge: float, label: str,
                   passes: int, backlash_check: bool = False,
                   tag: str = "") -> dict:
        """信号エッジ (X) の精密測定。x_edge は粗エッジ (絶対 X)。"""
        cfg = self._fine_cfg()
        step = self._pulse_um(0)
        offsets = [v - base[0] for v in frange(x_edge - cfg["x_half"],
                                               x_edge + cfg["x_half"], step)]
        centers, errs, sigmas, his = [], [], [], []
        for k in range(passes):
            xs, amps = self._sweep(
                0, base, offsets, f"{label} 精密 {k + 1}/{passes}",
                reads=cfg["reads"], settle=cfg["settle"],
                tag=f"{tag}:asc{k + 1}")
            f = fit_edge(xs, amps, falling=True)
            centers.append(f["center"])
            errs.append(f["err"])
            sigmas.append(f["sigma"])
            his.append(f["hi"])
        res = {"center": float(np.mean(centers)),
               "centers": [_r(c) for c in centers],
               "spread": float(max(centers) - min(centers)),
               "err": float(np.nanmean(errs)) if errs else float("nan"),
               "sigma": float(np.mean(sigmas)),
               "plateau_mv": float(np.mean(his)) * 1e3,
               "n_pass": passes, "n_points": len(offsets),
               "backlash": float("nan")}
        if backlash_check:
            xs, amps = self._sweep(
                0, base, offsets, f"{label} 逆方向 (バックラッシュ推定)",
                reads=cfg["reads"], settle=cfg["settle"], descending=True,
                tag=f"{tag}:desc")
            try:
                fd = fit_edge(xs, amps, falling=True)
                res["center_desc"] = fd["center"]
                res["backlash"] = fd["center"] - res["center"]
            except ValueError as exc:
                self._warnings.append(f"{label}: 逆方向掃引のフィット不可 "
                                      f"({exc})")
        return res

    def _focus_z(self, base: list[float], half: float, step: float,
                 label: str, fine: bool = False, passes: int = 1,
                 tag: str = "") -> tuple[float, float]:
        """シリコン上 (ストリップ横) で Z を掃引し、振幅最小の Z へ移動する。

        ★前提 (2026-09-07 実測で確定)★: フォーカスが合うほど振幅は
        「小さく」なる — 局所キャリア密度増による LGAD ゲイン抑制
        (プラズマ効果)。旧実装の「振幅最大 = 焦点」は誤り。
        ★場所 (2026-09-07 実機フィードバック)★: 電極は幅 ~50 µm あり、
        直上ではスポットがデフォーカスしても金属内に収まったまま
        (振幅 0 のまま変化せず谷が見えない)。**信号が明瞭に見える
        ストリップ横のシリコン上** (base は電極中心から probe_off 離した
        位置) で掃引する。
        粗モード: 50% 閾値の両翼エッジの中点 (dip_center)。
        精密モード (fine): 1 パルス刻み・多数回読みで passes 回掃引し、
        平均プロファイルをローレンツ型 + 一次基線でフィット → 曲線の
        最小点 (fit_focus)。フィットできなければ閾値法に戻す (警告)。
        戻り値: (移動先の Z, その位置での振幅)。移動先はステージ分解能
        (2 µm) に丸めた値。丸める前の谷中心は self._last_focus_exact に
        残す (フル校正の係数フィットは丸め前の値を使う: 丸めた値だけで
        フィットすると 3 点・数 µm の差に ±1 µm の量子化誤差が乗り、
        dZ/dY が 1 µm/mm 程度ずれる)。
        """
        cfg = self._fine_cfg()
        reads = settle = None
        if fine:
            step = self._pulse_um(2)
            reads, settle = cfg["reads"], cfg["settle"]
        offsets = frange(-half, half, step)
        center = base[2]
        best = None
        info: dict = {"method": "fit" if fine else "threshold"}
        # プレフォーカスずれで谷が掃引の端にかかったら、最小点を中心に
        # 据え直してリトライ (最大 2 回 → 初期ずれ ±2×range まで許容)
        for attempt in range(3):
            b = [base[0], base[1], center]
            prof = []
            for k in range(max(1, passes)):
                sub = (f" (再センタリング {attempt})" if attempt else "") + \
                      (f" {k + 1}/{passes}" if passes > 1 else "")
                zs, amps = self._sweep(2, b, offsets, label + sub,
                                       reads=reads, settle=settle,
                                       tag=f"{tag}:pass{k + 1}" if tag else "")
                prof.append((zs, amps))
            zs = prof[0][0]
            amps = np.mean([a for _, a in prof], axis=0)
            try:
                if fine:
                    try:
                        f = fit_focus(zs, amps, min_depth=0.004)
                        best = f["center"]
                        per_pass = []
                        for z_k, a_k in prof:
                            try:
                                per_pass.append(fit_focus(z_k, a_k)["center"])
                            except ValueError:
                                pass
                        info.update({
                            "center": best, "z0": f["z0"],
                            "gamma": f["gamma"],
                            "depth_pct": 100.0 * f["depth_ratio"],
                            "err": f["err"], "rms_mv": f["rms"] * 1e3,
                            "per_pass": [_r(v) for v in per_pass],
                            "spread": (max(per_pass) - min(per_pass))
                            if len(per_pass) > 1 else 0.0})
                    except ValueError as exc:
                        if _EDGE_MARKER in str(exc):
                            raise
                        best, _, _, _ = dip_center(
                            zs, amps, min_width=2.0, max_width=4.0 * half,
                            max_ratio=0.9, min_depth=0.004)
                        info.update({"method": "threshold", "center": best})
                        self._warnings.append(
                            f"{label}: 谷のフィット不可 ({exc}) → 50% 閾値法")
                else:
                    best, _, _, _ = dip_center(
                        zs, amps, min_width=2.0, max_width=4.0 * half,
                        max_ratio=0.9, min_depth=0.004)
                    info["center"] = best
                break
            except ValueError as exc:
                if _EDGE_MARKER in str(exc) and attempt < 2:
                    center = float(zs[int(np.argmin(_smooth3(amps)))])
                    continue
                raise ValueError(
                    f"Z フォーカス (振幅最小の谷) を特定できません: {exc}\n"
                    "→ Z 掃引範囲を広げる (±30〜50 µm)、ビームを信号が"
                    "明瞭なシリコン上に置く、またはカメラで粗フォーカスを"
                    "合わせてから再実行してください") from None
        # ステージ Z 分解能 (2 µm/pulse) に丸める (丸め前はフィット用に保持)
        self._last_focus_exact = float(best)
        self._last_focus_info = info
        best = self._quant(2, best)
        tgt = list(base)
        tgt[2] = best - self.p.get("backlash_um", 10.0)
        self._goto(*tgt)          # 下から接近して再現性を確保
        tgt[2] = best
        self._goto(*tgt)
        time.sleep(self.p.get("settle_s", 0.15))
        return best, self._amp()

    def _z_origin_from_probe(self, z_probe: float, dx: float,
                             dy: float) -> float:
        """プローブ位置 (原点から dx, dy) で測った合焦 Z → 原点での合焦 Z。

        傾き係数の事前値 (coef0) で面を外挿する微小補正 (標準センサーで
        dZ/dY 3.8 µm/mm × 80 µm ≈ 0.3 µm)。係数未較正なら補正 0。
        """
        return z_probe - (self._dzdx0 * dx + self._dzdy0 * dy)

    def _restore(self):
        """中断・エラー時の復帰: R:W 前なら開始位置、後なら原点へ。"""
        try:
            self.stage.stop()
        except Exception:
            pass
        try:
            if self._origin_done:
                self.stage.move_to_um_relative(0.0, 0.0, 0.0)
            elif hasattr(self, "_start_pos"):
                self.stage.move_to_um_relative(*self._start_pos)
        except Exception:
            pass

    def _write_csv(self):
        path = self.p.get("csv_path")
        if not path or not self._csv_rows:
            return
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([f"# 自動アライメント掃引記録 "
                            f"{datetime.datetime.now().isoformat()} "
                            f"mode={self.p.get('mode')}"])
                w.writerow(["tag", "axis", "idx", "ch", "x_um", "y_um",
                            "z_um", "amplitude_v"])
                for row in self._csv_rows:
                    w.writerow([row[0], row[1], row[2], row[3],
                                f"{row[4]:.1f}", f"{row[5]:.1f}",
                                f"{row[6]:.1f}", f"{row[7]:.6g}"])
        except Exception as exc:
            self._warnings.append(f"掃引記録 CSV を書けませんでした ({exc})")

    # ------------------------------------------------------------------
    # 検証 (R:W 後の原点再測定)
    # ------------------------------------------------------------------
    def _verify_origin(self, frame_off: float, x_ref: float, ywidth: float,
                       probe_off: float, x_probe_off: float, half_z: float,
                       tag: str) -> tuple[float, float, float, dict]:
        """新しい論理原点で電極中心 Y・信号エッジ X・焦点 Z を再測定する。

        戻り値: (dx, dy, dz, 詳細)。dx/dy/dz は「真の原点の現在の論理
        座標」= 残差。dz は傾き係数で原点位置へ換算済み。
        """
        # 電極面の Z は係数平面で追従 (原点で 0)
        def zp(x, y):
            return self._quant(2, self._dzdx0 * x + self._dzdy0 * y)
        e1, e2 = -0.5 * ywidth, 0.5 * ywidth
        fy = self._fine_dip([x_ref, 0.0, zp(x_ref, 0.0)], e1, e2,
                            "検証 Y", 1, tag=f"{tag}:y")
        dy = fy["center"]
        base = [-frame_off, dy - x_probe_off, zp(-frame_off, dy)]
        fx = self._fine_edge(base, -frame_off, "検証 X", 1, tag=f"{tag}:x")
        dx = fx["center"] + frame_off
        zb = [dx - frame_off - 30.0, dy - probe_off, 0.0]
        zb[2] = zp(zb[0], zb[1])
        self._focus_z(zb, half_z, self._pulse_um(2), "検証 Z", fine=True,
                      passes=1, tag=f"{tag}:z")
        z_probe = self._last_focus_exact
        dz = self._z_origin_from_probe(z_probe, zb[0] - dx, zb[1] - dy)
        detail = {"dx": _r(dx), "dy": _r(dy), "dz": _r(dz),
                  "y_err": _r(fy["err"]), "x_err": _r(fx["err"]),
                  "z_info": {k: (_r(v) if isinstance(v, float) else v)
                             for k, v in self._last_focus_info.items()},
                  "z_probe_exact": z_probe, "ywidth": _r(fy["width"])}
        return dx, dy, dz, detail

    # ------------------------------------------------------------------
    # 本体
    # ------------------------------------------------------------------
    def _run(self):
        if str(self.p.get("sensor_type", "strip")) == "pixel":
            return self._run_pixel()
        p = self.p
        fine = bool(p.get("fine", False))
        cfg = self._fine_cfg()
        results: dict = {"method": "signal-based alignment"
                         + (" (fine)" if fine else ""),
                         "ch": p["ch"],
                         "measured_at": datetime.datetime.now().isoformat()}

        # --- 準備: レーザー / オシロを振幅読み取りモードに ---
        if p.get("laser_on") and self.laser.connected:
            self.laser.set_emission(True)
            self._laser_turned_on = True
            time.sleep(1.0)
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        pos, _ = self.stage.get_position_um()
        self._start_pos = list(pos)
        if p.get("pre_move_xy") is not None:
            # 原点出し済みの論理原点 (外枠エッジ = 信号なし) にいる状態から
            # でも始められるよう、基準ストリップ上の開始位置へ寄せる
            self.progress.emit(0, 0, "開始位置 (基準ストリップ上) へ移動")
            x0, y0 = p["pre_move_xy"]
            self._goto(x0, y0, pos[2])
            pos, _ = self.stage.get_position_um()
        cur = list(pos)
        y_half = p.get("y_half_um", 150.0)
        y_step = p.get("y_step_um", 3.0)
        af_half = p.get("af_range_um", 20.0)
        af_step = p.get("af_step_um", 2.0)
        # Z フォーカスは電極中心から probe_off (80 µm) 横のシリコン上で
        # 行う (電極幅 ~50 µm=半幅 25 + デフォーカス時のスポット拡がりの
        # 余裕。信号が明瞭に見える位置)。
        # X エッジ検出は x_probe_off (50 µm) を使う: 80 µm ではボンド
        # パッドの Y 幅から外れて信号が消えず「明確なエッジがない」と
        # なった (2026-09-07 実機フィードバック)。パッド金属が確実に
        # かかる電極寄りの位置で走査する
        probe_off = p.get("y_probe_off_um", 80.0)
        x_probe_off = p.get("x_probe_off_um", 50.0)
        n_steps = 6 if fine else 5

        # === 1. Y 粗ディップスキャン (現在の Z のまま電極位置に当たり) ===
        # デフォーカスでディップが浅くなるため判定を緩める (max_ratio 0.75)
        self.progress.emit(0, 0, f"Step 1/{n_steps}: Y 電極ディップ粗検出")
        ys, amps = self._sweep(
            1, cur, frange(-y_half, y_half, y_step), "Y 粗スキャン",
            tag="origin:y_coarse")
        yc0, _, _, _ = dip_center(ys, amps, max_ratio=0.75)

        # === 2. ストリップ横のシリコン上で Z フォーカス (振幅最小) ===
        self.progress.emit(0, 0, f"Step 2/{n_steps}: Z フォーカス "
                                 "(シリコン上・振幅最小)")
        base = [cur[0], yc0 - probe_off, cur[2]]
        best_z, amp_foc = self._focus_z(base, af_half, af_step,
                                        "Z フォーカス", tag="origin:z1")
        cur[2] = best_z
        results["amp_at_focus_mv"] = round(amp_foc * 1e3, 1)

        # === 3. Y 精密ディップスキャン (フォーカス後はエッジが鋭い) ===
        self.progress.emit(0, 0, f"Step 3/{n_steps}: Y 電極ディップ精密検出")
        base = [cur[0], yc0, best_z]
        half3 = min(y_half, 60.0)
        ys, amps = self._sweep(
            1, base, frange(-half3, half3, y_step), "Y スキャン",
            tag="origin:y_mid")
        yc, ywidth, e1, e2 = dip_center(ys, amps)
        fine_y = fine_x = None
        if fine:
            # ★精密仕上げ: 1 パルス刻み・多数回読み・erf フィット★
            base = [cur[0], yc, best_z]
            try:
                fine_y = self._fine_dip(
                    base, e1, e2, "Y 電極中心", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:y_fine")
                yc, ywidth = fine_y["center"], fine_y["width"]
            except ValueError as exc:
                self._warnings.append(f"Y 精密仕上げ不可 ({exc}) → 粗検出値")
        results["electrode_width_um"] = round(ywidth, 1)
        cur[1] = yc

        # === 4. X エッジスキャン → 読み出し側端 (②/③境界) を検出 ===
        self.progress.emit(0, 0, f"Step 4/{n_steps}: X 端 (読み出し側) 検出")
        probe = [cur[0], yc - x_probe_off, cur[2]]
        xs, amps = self._sweep(
            0, probe, frange(-p.get("x_back_um", 30.0),
                             p.get("x_fwd_um", 250.0),
                             p.get("x_step_um", 4.0)), "X スキャン",
            tag="origin:x_coarse")
        x_edge, hi, lo = edge_position(xs, amps, falling=True)
        results["plateau_mv"] = round(hi * 1e3, 1)
        if fine:
            base = [x_edge, yc - x_probe_off, cur[2]]
            try:
                fine_x = self._fine_edge(
                    base, x_edge, "X 信号エッジ", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:x_fine")
                x_edge = fine_x["center"]
            except ValueError as exc:
                self._warnings.append(f"X 精密仕上げ不可 ({exc}) → 粗検出値")

        # === 5. Z 再確認 (シリコン上) → 外枠エッジへ移動して原点リセット ===
        self.progress.emit(0, 0, f"Step 5/{n_steps}: Z 再確認と原点リセット")
        # 端から 30 µm 内側・電極横のシリコン上で振幅最小を再確認する
        # (谷の両翼が要るため掃引範囲は縮めない)
        frame_off = float(p.get("x_frame_offset_um", 632.0))
        frame_x = x_edge + frame_off
        chk = [x_edge - 30.0, yc - probe_off, cur[2]]
        self._goto(*chk)
        self._focus_z(chk, af_half, af_step, "Z 再確認", fine=fine,
                      passes=cfg["passes"] if fine else 1, tag="origin:z2")
        fine_z = dict(self._last_focus_info) if fine else None
        # 原点の Z: プローブ位置の谷中心を電極中心上 (frame_x, yc) へ
        # 傾き係数で換算 (微小) → パルス格子に丸める
        z_exact = self._z_origin_from_probe(
            self._last_focus_exact, chk[0] - frame_x, chk[1] - yc)
        z_t = self._quant(2, z_exact)
        # 原点 = 信号エッジ (②/③境界) + 設計オフセット 632 µm = 外枠エッジ
        cur = [frame_x, yc, z_t]
        self._approach(cur)      # 全軸とも掃引と同じ負側から進入
        if p.get("reset_origin", True):
            self.stage.zero_all()          # R:W → 外枠エッジが (0,0,0)
            self._origin_done = self.origin_done = True
        # フル校正用: yc を実測した X 位置 (新原点基準の論理座標) と、
        # 原点の合焦 Z の丸め残り (プローブ位置の谷中心, 新原点基準)
        x_ref = pos[0] - frame_x
        z0_frac = self._last_focus_exact - z_t
        residual = [frame_x - cur[0], 0.0, z_exact - z_t]  # 丸め残り (推定)
        results["origin_quantization_um"] = [_r(v) for v in residual]

        # === 6. 検証: R:W 後に原点を再測定し、1 パルス以上なら補正 ===
        verify: dict | None = None
        if fine and p.get("verify", True) and self._origin_done:
            max_iter = int(p.get("verify_max_iter", 2))
            corrections: list[list[float]] = []
            details: list[dict] = []
            residual = None
            for it in range(max_iter + 1):
                self.progress.emit(
                    0, 0, f"Step 6/{n_steps}: 原点の検証 {it + 1}"
                          f" (再測定 Y→X→Z)")
                dx, dy, dz, det = self._verify_origin(
                    frame_off, x_ref, ywidth, probe_off, x_probe_off,
                    af_half, tag=f"verify{it + 1}")
                details.append(det)
                corr = [self._quant(i, v) for i, v in enumerate((dx, dy, dz))]
                if any(c != 0.0 for c in corr) and it < max_iter:
                    self.progress.emit(
                        0, 0, f"検証 {it + 1}: 残差 X={dx:+.1f} Y={dy:+.1f} "
                              f"Z={dz:+.1f} µm → 補正して R:W")
                    self._approach(corr)
                    self.stage.zero_all()
                    corrections.append(corr)
                    x_ref -= corr[0]
                    continue
                residual = [dx, dy, dz]
                z0_frac = det["z_probe_exact"]
                if any(c != 0.0 for c in corr):
                    self._warnings.append(
                        f"検証 {it + 1} 回目でも残差が 1 パルス以上 "
                        f"(X={dx:+.1f} Y={dy:+.1f} Z={dz:+.1f} µm)。"
                        "再現性が悪い可能性 — 掃引記録を確認してください")
                break
            verify = {"n_measure": len(details),
                      "corrections_um": corrections,
                      "residual_um": [_r(v) for v in residual],
                      "details": details}
            results["origin_residual_um"] = verify["residual_um"]
            # 原点へ戻る (検証は電極上で終わるため)
            self._approach([0.0, 0.0, 0.0])
        self._x_ref = x_ref
        results["x_yc_measured_um"] = round(x_ref, 1)

        # --- 原点出しの報告 ---
        if fine:
            results["fine"] = {
                "y": {k: (_r(v) if isinstance(v, float) else v)
                      for k, v in (fine_y or {}).items()},
                "x": {k: (_r(v) if isinstance(v, float) else v)
                      for k, v in (fine_x or {}).items()},
                "z": {k: (_r(v) if isinstance(v, float) else v)
                      for k, v in (fine_z or {}).items()},
                "reads": cfg["reads"], "passes": cfg["passes"]}
            results["backlash_um"] = {
                "y": _r((fine_y or {}).get("backlash", float("nan"))),
                "x": _r((fine_x or {}).get("backlash", float("nan")))}
            results["verify"] = verify
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.origin_report = {k: results[k] for k in
                              ("fine", "backlash_um", "verify",
                               "origin_residual_um", "warnings")
                              if k in results} or None

        origin_msg = (
            f"原点出し完了 (電極幅 {ywidth:.0f} µm)\n"
            f"電極中心 Y, "
            f"信号エッジ+{frame_off:.0f} µm = "
            f"外枠エッジ, フォーカス Z (焦点での振幅 {amp_foc * 1e3:.0f} mV"
            f" — ゲイン抑制で最小) "
            f"{'→ 論理原点リセット済み (R:W)' if self._origin_done else ''}\n"
            f"ストリップ露出部は X=-{frame_off:.0f} µm から始まります")
        if fine:
            origin_msg += "\n" + self._fine_summary(fine_y, fine_x, fine_z,
                                                   verify, cfg)
        if self._warnings:
            origin_msg += "\n★注意: " + " / ".join(self._warnings)

        if p.get("mode", "origin") != "full":
            self.finished_align.emit(True, origin_msg)
            return

        # ==============================================================
        # フル校正: 追加測定点で係数をフィット (座標は新しい論理原点基準)
        # ==============================================================
        dzdx0, dzdy0, dydx0, src0 = p.get(
            "coef0", (0.0, 0.0, 0.0, "未較正"))
        # 原点出しで電極中心を実測した X (新原点基準)。電極ラインは
        # (x_ref, 0) を通るので、予測はここを基準に傾き分を外挿する
        x_rows = [(x_ref, 0.0, z0_frac)]   # (x, y_electrode_center, z_focus)
        point_fits: list[dict] = []
        for k, x in enumerate(p.get("x_points", [-5300.0, -9900.0])):
            x = float(x)
            self.progress.emit(
                0, 0, f"フル校正: X 測定点 {k + 1} (X={x:.0f} µm)")
            y_pred = dydx0 * (x - x_ref)
            z_pred = self._quant(2, dzdx0 * (x - x_ref))
            # 傾き予測の Z でディップ検出 → 電極中心上で Z 最小化
            base = [x, y_pred, z_pred]
            yc, e1, e2, info = self._point_dip(
                base, y_half, y_step, f"Y スキャン @X={x:.0f}", fine,
                tag=f"cal:x{k + 1}")
            base[1] = yc - probe_off        # シリコン上でフォーカス
            self._focus_z(base, af_half, af_step,
                          f"Z フォーカス @X={x:.0f}", fine=fine,
                          tag=f"cal:x{k + 1}:z")
            x_rows.append((x, yc, self._last_focus_exact))
            info.update({"x": x, "z": _r(self._last_focus_exact),
                         "z_info": dict(self._last_focus_info)})
            point_fits.append(info)

        y_rows = [(0.0, z0_frac)]      # (y_electrode_center, z_focus)
        # ストリップ露出部 (X < -632) の内側、原点出しと同じ X 付近で測る
        x_probe = p.get("x_probe_um", x_ref)
        # ★Y 測定点ごとに、そのストリップを読むオシロ CH に切り替える★
        # 固定 CH (例 C1) のままだと Y=-500/-1500 (C2/C4 ストリップ) では
        # 信号がほぼ無く、係数スキャンが失敗する (2026-09-07 実機で確認)。
        # どの Y をどの CH で読むかはセンサープロファイルの読み出し表から
        # 来る (params["y_points"] = [[y, CH], ...])
        y_points = p.get("y_points", [[-500.0, "C2"], [-1500.0, "C4"]])
        y_half2 = min(120.0, y_half)
        used_channels = [self.p.get("ch", "C1")]
        for k, (y, strip_ch) in enumerate(y_points):
            self._ch = strip_ch
            used_channels.append(strip_ch)
            self.progress.emit(
                0, 0, f"フル校正: Y 測定点 {k + 1} "
                      f"(Y={y:.0f} µm, CH={strip_ch})")
            z_pred = self._quant(2, dzdy0 * y)
            # 傾き予測の Z でディップ検出 → シリコン上で Z 最小化
            base = [x_probe, y, z_pred]
            yc, e1, e2, info = self._point_dip(
                base, y_half2, y_step, f"Y スキャン @Y={y:.0f} ({strip_ch})",
                fine, tag=f"cal:y{k + 1}")
            base[1] = yc - probe_off        # シリコン上でフォーカス
            self._focus_z(base, af_half, af_step,
                          f"Z フォーカス @Y={y:.0f} ({strip_ch})", fine=fine,
                          tag=f"cal:y{k + 1}:z")
            y_rows.append((yc, self._last_focus_exact))
            info.update({"y_nominal": y, "ch": strip_ch,
                         "z": _r(self._last_focus_exact),
                         "z_info": dict(self._last_focus_info)})
            point_fits.append(info)
        self._ch = self.p.get("ch", "C1")   # 基準 CH に戻す
        results["y_strip_channels"] = used_channels[1:]

        # --- フィット (原点も含む) ---
        # 測定点が無い方向 (読み出し 1 ch のみ等) は前回の係数を引き継ぐ
        xa = np.array([r[0] for r in x_rows])
        ya = np.array([r[0] for r in y_rows])
        dydx, dzdx, dzdy = dydx0, dzdx0, dzdy0
        if len(y_rows) >= 2:
            dzdy = float(np.polyfit(ya, [r[1] for r in y_rows], 1)[0])
        if len(x_rows) >= 2:
            dydx = float(np.polyfit(xa, [r[1] for r in x_rows], 1)[0])
            # X 測定点の Z は電極線 (Y が dY/dX で流れる) に沿って測って
            # いるので、その傾きは dZ/dX + dZ/dY·dY/dX。スキャンの平面
            # モデル z = dZ/dX·x + dZ/dY·y が使うのは Y 一定の偏微分なので
            # 結合項を除く (標準センサーで 0.03 µm/mm の微小補正)
            dzdx = float(np.polyfit(xa, [r[2] for r in x_rows], 1)[0])                 - dzdy * dydx
        # ピッチ実測: 電極中心 Y vs ストリップ番号 (公称ピッチで割って同定)
        nominal = float(p.get("pitch_um", 500.0))
        idx = [0.0] + [round(-y / nominal) for y, _ in y_points]
        pitch = float(-np.polyfit(idx, [r[0] for r in y_rows], 1)[0]) \
            if len(y_rows) >= 2 else float("nan")

        # フィット残差 (Z は 2 µm 刻みでしか決まらないので、残差が
        # 数 µm を超えるなら谷の検出が怪しい → 結果表示で判断材料にする)
        def _resid(xs, ys, slope):
            xs, ys = np.asarray(xs, float), np.asarray(ys, float)
            r = ys - slope * xs
            return float(np.max(np.abs(r - r.mean())))
        res_y = _resid(xa, [r[1] for r in x_rows], dydx)
        res_zx = _resid(xa, [r[2] for r in x_rows], dzdx)
        res_zy = _resid(ya, [r[1] for r in y_rows], dzdy)

        results.update({
            "dzdx": dzdx, "dzdy": dzdy, "dydx": dydx,
            "x_rows_um": [[round(v, 2) for v in r] for r in x_rows],
            "y_rows_um": [[round(v, 2) for v in r] for r in y_rows],
            "strip_pitch_measured_um": round(pitch, 1),
            "fit_residual_um": {"y_vs_x": round(res_y, 2),
                                "z_vs_x": round(res_zx, 2),
                                "z_vs_y": round(res_zy, 2)},
            "previous": {"dzdx": dzdx0, "dzdy": dzdy0, "dydx": dydx0,
                         "source": src0},
        })
        if fine:
            results["point_fits"] = point_fits
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.calibration = results

        # 原点へ戻る (Z は掃引と同じく下から接近して再現性を確保)
        self._approach([0.0, 0.0, 0.0])
        msg = (f"{origin_msg}\n"
               f"フル校正完了 — 係数を更新しました:\n"
               f"  dY/dX = {dydx * 1000:+.2f} µm/mm "
               f"(前回 {dydx0 * 1000:+.2f})\n"
               f"  dZ/dX = {dzdx * 1000:+.2f} µm/mm "
               f"(前回 {dzdx0 * 1000:+.2f})\n"
               f"  dZ/dY = {dzdy * 1000:+.2f} µm/mm "
               f"(前回 {dzdy0 * 1000:+.2f})\n"
               f"  隣接電極間隔 (実測): {pitch:.0f} µm "
               f"(公称 {nominal:.0f})\n"
               f"  直線フィット残差 (最大): Y(X) ±{res_y:.1f} µm, "
               f"Z(X) ±{res_zx:.1f} µm, Z(Y) ±{res_zy:.1f} µm\n"
               f"以後のスキャンはこの係数を自動で使います")
        self.finished_align.emit(True, msg)

    # ------------------------------------------------------------------
    # ピクセル型 (パッド = 無信号の正方形 を物差しにする)
    # ------------------------------------------------------------------
    def _verify_origin_pixel(self, pad: float, ywidth: float, xwidth: float,
                             probe_off: float, half_z: float,
                             tag: str) -> tuple[float, float, float, dict]:
        """新しい論理原点で基準パッドの中心 (−pad/2, −pad/2) と焦点 Z を再測定する。
        戻り値: (dx, dy, dz, 詳細) = 真の原点の現在の論理座標 (残差)。"""
        def zp(x, y):
            return self._quant(2, self._dzdx0 * x + self._dzdy0 * y)
        cx = cy = -pad / 2.0
        mw = 2.0 * pad
        fy = self._fine_dip([cx, cy, zp(cx, cy)], cy - 0.5 * ywidth,
                            cy + 0.5 * ywidth, "検証 Y", 1, tag=f"{tag}:y",
                            axis=1, max_width=mw)
        yc = fy["center"]
        dy = yc - cy
        fx = self._fine_dip([cx, yc, zp(cx, yc)], cx - 0.5 * xwidth,
                            cx + 0.5 * xwidth, "検証 X", 1, tag=f"{tag}:x",
                            axis=0, max_width=mw)
        xc = fx["center"]
        dx = xc - cx
        zb = [xc, yc - probe_off, 0.0]
        zb[2] = zp(zb[0], zb[1])
        self._focus_z(zb, half_z, self._pulse_um(2), "検証 Z", fine=True,
                      passes=1, tag=f"{tag}:z")
        z_probe = self._last_focus_exact
        dz = self._z_origin_from_probe(z_probe, zb[0] - dx, zb[1] - dy)
        detail = {"dx": _r(dx), "dy": _r(dy), "dz": _r(dz),
                  "y_err": _r(fy["err"]), "x_err": _r(fx["err"]),
                  "z_info": {k: (_r(v) if isinstance(v, float) else v)
                             for k, v in self._last_focus_info.items()},
                  "z_probe_exact": z_probe, "ywidth": _r(fy["width"]),
                  "xwidth": _r(fx["width"])}
        return dx, dy, dz, detail

    def _run_pixel(self):
        """ピクセル型の原点出し (+ フル較正)。モジュール docstring の「ピクセル型」参照。"""
        p = self.p
        fine = bool(p.get("fine", False))
        cfg = self._fine_cfg()
        pad = float(p.get("pad_um", 100.0))
        pitch = float(p.get("pitch_um", 500.0))
        mw = 2.0 * pad                      # ディップ幅の上限 (デフォーカスで広がる分の余裕)
        results: dict = {"method": "signal-based alignment (pixel)"
                         + (" (fine)" if fine else ""),
                         "ch": p["ch"], "pad_um": pad,
                         "measured_at": datetime.datetime.now().isoformat()}

        if p.get("laser_on") and self.laser.connected:
            self.laser.set_emission(True)
            self._laser_turned_on = True
            time.sleep(1.0)
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        pos, _ = self.stage.get_position_um()
        self._start_pos = list(pos)
        if p.get("pre_move_xy") is not None:
            self.progress.emit(0, 0, "開始位置 (基準パッドの中心) へ移動")
            x0, y0 = p["pre_move_xy"]
            self._goto(x0, y0, pos[2])
            pos, _ = self.stage.get_position_um()
        cur = list(pos)
        y_half = float(p.get("y_half_um", pad + 100.0))
        y_step = float(p.get("y_step_um", 3.0))
        x_step = float(p.get("x_step_um", 4.0))
        af_half = p.get("af_range_um", 20.0)
        af_step = p.get("af_step_um", 2.0)
        probe_off = float(p.get("y_probe_off_um", pad / 2.0 + 80.0))
        n_steps = 6 if fine else 5

        # === 1. Y 粗ディップ (パッドを横切る) ===
        self.progress.emit(0, 0, f"Step 1/{n_steps}: Y パッドディップ粗検出")
        ys, amps = self._sweep(1, cur, frange(-y_half, y_half, y_step),
                               "Y 粗スキャン (パッド)", tag="origin:y_coarse")
        yc0, _, _, _ = dip_center(ys, amps, max_ratio=0.75, max_width=mw + 50.0)

        # === 2. パッド横 (ピクセル内のシリコン上) で Z フォーカス ===
        self.progress.emit(0, 0, f"Step 2/{n_steps}: Z フォーカス (パッド横・振幅最小)")
        base = [cur[0], yc0 - probe_off, cur[2]]
        best_z, amp_foc = self._focus_z(base, af_half, af_step, "Z フォーカス",
                                        tag="origin:z1")
        cur[2] = best_z
        results["amp_at_focus_mv"] = round(amp_foc * 1e3, 1)

        # === 3. Y 精密ディップ ===
        self.progress.emit(0, 0, f"Step 3/{n_steps}: Y パッド中心の精密検出")
        half3 = min(y_half, pad / 2.0 + 60.0)
        ys, amps = self._sweep(1, [cur[0], yc0, best_z],
                               frange(-half3, half3, y_step), "Y スキャン (パッド)",
                               tag="origin:y_mid")
        yc, ywidth, e1, e2 = dip_center(ys, amps, max_width=mw)
        fine_y = fine_x = None
        if fine:
            try:
                fine_y = self._fine_dip(
                    [cur[0], yc, best_z], e1, e2, "Y パッド中心", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:y_fine", axis=1, max_width=mw)
                yc, ywidth = fine_y["center"], fine_y["width"]
            except ValueError as exc:
                self._warnings.append(f"Y 精密仕上げ不可 ({exc}) → 粗検出値")
        results["pad_width_y_um"] = round(ywidth, 1)
        cur[1] = yc

        # === 4. X ディップ (パッド中心の行で X を横切る) ===
        self.progress.emit(0, 0, f"Step 4/{n_steps}: X パッド中心の検出")
        half4 = pad / 2.0 + 100.0
        xs, amps = self._sweep(0, [cur[0], yc, best_z],
                               frange(-half4, half4, x_step), "X スキャン (パッド)",
                               tag="origin:x_coarse")
        xc, xwidth, ex1, ex2 = dip_center(xs, amps, max_width=mw)
        if fine:
            try:
                fine_x = self._fine_dip(
                    [xc, yc, best_z], ex1, ex2, "X パッド中心", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:x_fine", axis=0, max_width=mw)
                xc, xwidth = fine_x["center"], fine_x["width"]
            except ValueError as exc:
                self._warnings.append(f"X 精密仕上げ不可 ({exc}) → 粗検出値")
        results["pad_width_x_um"] = round(xwidth, 1)

        # === 5. Z 再確認 → パッドの +X/+Y 側の角で原点リセット ===
        self.progress.emit(0, 0, f"Step 5/{n_steps}: Z 再確認と原点リセット")
        corner = [xc + pad / 2.0, yc + pad / 2.0]
        chk = [xc, yc - probe_off, best_z]
        self._goto(*chk)
        self._focus_z(chk, af_half, af_step, "Z 再確認", fine=fine,
                      passes=cfg["passes"] if fine else 1, tag="origin:z2")
        fine_z = dict(self._last_focus_info) if fine else None
        z_exact = self._z_origin_from_probe(
            self._last_focus_exact, chk[0] - corner[0], chk[1] - corner[1])
        z_t = self._quant(2, z_exact)
        cur = [corner[0], corner[1], z_t]
        self._approach(cur)
        if p.get("reset_origin", True):
            self.stage.zero_all()          # R:W → パッドの角が (0,0,0)
            self._origin_done = self.origin_done = True
        x_ref = -pad / 2.0                 # 新原点基準のパッド中心 X
        z0_frac = self._last_focus_exact - z_t
        results["origin_quantization_um"] = [_r(v) for v in (0.0, 0.0, z_exact - z_t)]
        results["pad_center_measured_um"] = [_r(xc - corner[0]), _r(yc - corner[1])]

        # === 6. 検証 ===
        verify: dict | None = None
        if fine and p.get("verify", True) and self._origin_done:
            max_iter = int(p.get("verify_max_iter", 2))
            corrections: list[list[float]] = []
            details: list[dict] = []
            residual = None
            for it in range(max_iter + 1):
                self.progress.emit(
                    0, 0, f"Step 6/{n_steps}: 原点の検証 {it + 1} (再測定 Y→X→Z)")
                dx, dy, dz, det = self._verify_origin_pixel(
                    pad, ywidth, xwidth, probe_off, af_half, tag=f"verify{it + 1}")
                details.append(det)
                corr = [self._quant(i, v) for i, v in enumerate((dx, dy, dz))]
                if any(c != 0.0 for c in corr) and it < max_iter:
                    self.progress.emit(
                        0, 0, f"検証 {it + 1}: 残差 X={dx:+.1f} Y={dy:+.1f} "
                              f"Z={dz:+.1f} µm → 補正して R:W")
                    self._approach(corr)
                    self.stage.zero_all()
                    corrections.append(corr)
                    continue
                residual = [dx, dy, dz]
                z0_frac = det["z_probe_exact"]
                if any(c != 0.0 for c in corr):
                    self._warnings.append(
                        f"検証 {it + 1} 回目でも残差が 1 パルス以上 "
                        f"(X={dx:+.1f} Y={dy:+.1f} Z={dz:+.1f} µm)。"
                        "再現性が悪い可能性 — 掃引記録を確認してください")
                break
            verify = {"n_measure": len(details), "corrections_um": corrections,
                      "residual_um": [_r(v) for v in residual], "details": details}
            results["origin_residual_um"] = verify["residual_um"]
            self._approach([0.0, 0.0, 0.0])
        self._x_ref = x_ref
        results["x_yc_measured_um"] = round(x_ref, 1)
        if fine:
            results["fine"] = {
                "y": {k: (_r(v) if isinstance(v, float) else v) for k, v in (fine_y or {}).items()},
                "x": {k: (_r(v) if isinstance(v, float) else v) for k, v in (fine_x or {}).items()},
                "z": {k: (_r(v) if isinstance(v, float) else v) for k, v in (fine_z or {}).items()},
                "reads": cfg["reads"], "passes": cfg["passes"]}
            results["backlash_um"] = {
                "y": _r((fine_y or {}).get("backlash", float("nan"))),
                "x": _r((fine_x or {}).get("backlash", float("nan")))}
            results["verify"] = verify
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.origin_report = {k: results[k] for k in
                              ("fine", "backlash_um", "verify", "origin_residual_um", "warnings")
                              if k in results} or None

        origin_msg = (
            f"原点出し完了 (ピクセル: パッド幅 X {xwidth:.0f} / Y {ywidth:.0f} µm)\n"
            f"パッド中心 → +X/+Y 側の角 (+{pad / 2:.0f}, +{pad / 2:.0f} µm) を原点、"
            f"フォーカス Z (焦点での振幅 {amp_foc * 1e3:.0f} mV — ゲイン抑制で最小) "
            f"{'→ 論理原点リセット済み (R:W)' if self._origin_done else ''}\n"
            f"ピクセル中心 (パッド中心) は ({-pad / 2:.0f}, {-pad / 2:.0f}) µm、"
            f"隣のピクセルは ±{pitch:.0f} µm、4 ピクセル交点は "
            f"({-pad / 2 - pitch / 2:.0f}, {-pad / 2 - pitch / 2:.0f}) µm")
        if fine:
            origin_msg += "\n" + self._fine_summary(fine_y, fine_x, fine_z, verify, cfg)
        if self._warnings:
            origin_msg += "\n★注意: " + " / ".join(self._warnings)

        if p.get("mode", "origin") != "full":
            self.finished_align.emit(True, origin_msg)
            return

        # ==============================================================
        # フル較正: 他の読み出しピクセルのパッド中心 (その CH で読む)
        # ==============================================================
        dzdx0, dzdy0, dydx0, src0 = p.get("coef0", (0.0, 0.0, 0.0, "未較正"))
        pads = [list(v) for v in p.get("pads", [])]
        rows = [(x_ref, -pad / 2.0, z0_frac)]     # (パッド中心 x, y, 合焦 z) — 基準パッド
        nominal = [(x_ref, -pad / 2.0)]
        point_fits: list[dict] = []
        for k, (ch, xn, yn) in enumerate(pads):
            xn, yn = float(xn), float(yn)
            self._ch = str(ch)
            self.progress.emit(0, 0, f"フル較正: パッド {k + 1}/{len(pads)} ({ch}, X={xn:.0f}, Y={yn:.0f} µm)")
            y_pred = yn + dydx0 * (xn - x_ref)
            z_pred = self._quant(2, dzdx0 * xn + dzdy0 * y_pred)
            yc_k, _e1, _e2, info = self._point_dip(
                [xn, y_pred, z_pred], pad / 2.0 + 60.0, y_step, f"Y スキャン @{ch}",
                fine, tag=f"cal:{ch}", axis=1, max_width=mw)
            xc_k, _e1, _e2, info_x = self._point_dip(
                [xn, yc_k, z_pred], pad / 2.0 + 60.0, x_step, f"X スキャン @{ch}",
                fine, tag=f"cal:{ch}:x", axis=0, max_width=mw)
            base = [xc_k, yc_k - probe_off, z_pred]
            self._focus_z(base, af_half, af_step, f"Z フォーカス @{ch}", fine=fine,
                          tag=f"cal:{ch}:z")
            z_c = self._z_origin_from_probe(self._last_focus_exact, 0.0, -probe_off)
            rows.append((xc_k, yc_k, z_c))
            nominal.append((xn, yn))
            info.update({"ch": str(ch), "x_nominal": xn, "y_nominal": yn,
                         "x": _r(xc_k), "y": _r(yc_k), "z": _r(z_c),
                         "x_info": info_x, "z_info": dict(self._last_focus_info)})
            point_fits.append(info)
        self._ch = self.p.get("ch", "C1")

        # --- フィット ---
        pts = np.array(rows, float)
        dzdx, dzdy, dydx = dzdx0, dzdy0, dydx0
        res_z = float("nan")
        if len(rows) >= 3:
            A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(rows))])
            if np.linalg.matrix_rank(A) >= 3:
                coef, *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
                dzdx, dzdy = float(coef[0]), float(coef[1])
                res_z = float(np.max(np.abs(pts[:, 2] - A @ coef)))
        elif len(rows) == 2:
            dx_, dy_, dz_ = pts[1] - pts[0]
            if abs(dx_) > abs(dy_) and abs(dx_) > 1.0:
                dzdx = float(dz_ / dx_)
            elif abs(dy_) > 1.0:
                dzdy = float(dz_ / dy_)
        # 面内回転: 基準と同じ行 (公称 y が同じ) のパッド中心 Y vs X
        same_row = [i for i, (xn, yn) in enumerate(nominal) if abs(yn - (-pad / 2.0)) < pitch / 2.0]
        res_y = float("nan")
        if len(same_row) >= 2:
            xs_ = pts[same_row, 0]
            ys_ = pts[same_row, 1]
            dydx = float(np.polyfit(xs_, ys_, 1)[0])
            r_ = ys_ - dydx * xs_
            res_y = float(np.max(np.abs(r_ - r_.mean())))
        # ピッチ実測: 基準パッド中心からの距離 / 公称の段数
        pitches = []
        for i in range(1, len(rows)):
            d = math.hypot(pts[i, 0] - pts[0, 0], pts[i, 1] - pts[0, 1])
            d0 = math.hypot(nominal[i][0] - nominal[0][0], nominal[i][1] - nominal[0][1])
            n_ = round(d0 / pitch) if pitch > 0 else 0
            if n_ >= 1 and abs(d0 / pitch - n_) < 0.1:
                pitches.append(d / n_)
            elif d0 > 0:
                pitches.append(d / d0 * pitch)
        pitch_meas = float(np.mean(pitches)) if pitches else float("nan")
        results.update({
            "dzdx": dzdx, "dzdy": dzdy, "dydx": dydx,
            "pad_rows_um": [[round(v, 2) for v in r] for r in rows],
            "pad_nominal_um": [[round(v, 2) for v in r] for r in nominal],
            "pad_channels": [str(c[0]) for c in pads],
            "pixel_pitch_measured_um": round(pitch_meas, 1),
            "fit_residual_um": {"z_plane": _r(res_z), "y_vs_x": _r(res_y)},
            "previous": {"dzdx": dzdx0, "dzdy": dzdy0, "dydx": dydx0, "source": src0},
        })
        if fine:
            results["point_fits"] = point_fits
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.calibration = results

        self._approach([0.0, 0.0, 0.0])
        msg = (f"{origin_msg}\n"
               f"フル較正完了 (ピクセル: パッド {len(rows)} 点、レバーアーム ≈ {pitch:.0f} µm) — 係数を更新しました:\n"
               f"  dY/dX = {dydx * 1000:+.2f} µm/mm (前回 {dydx0 * 1000:+.2f})\n"
               f"  dZ/dX = {dzdx * 1000:+.2f} µm/mm (前回 {dzdx0 * 1000:+.2f})\n"
               f"  dZ/dY = {dzdy * 1000:+.2f} µm/mm (前回 {dzdy0 * 1000:+.2f})\n"
               f"  ピクセルピッチ (実測): {pitch_meas:.0f} µm (公称 {pitch:.0f})\n"
               f"  フィット残差 (最大): Z 平面 ±{res_z:.1f} µm, Y(X) ±{res_y:.1f} µm\n"
               "★レバーアームが 1 ピッチしか無いので係数は粗い目安です (スキャン範囲 1 mm 以下では影響 1 パルス未満)\n"
               "以後のスキャンはこの係数を自動で使います")
        self.finished_align.emit(True, msg)

    def _point_dip(self, base: list[float], half: float, step: float,
                   label: str, fine: bool, tag: str, axis: int = 1,
                   max_width: float = 200.0):
        """フル校正の測定点: 粗ディップ検出 (+ 精密仕上げ)。axis = 1 (Y) / 0 (X)。

        戻り値: (電極 / パッド中心, 粗左エッジ, 粗右エッジ, 情報 dict)
        """
        ys, amps = self._sweep(axis, base, frange(-half, half, step), label,
                               tag=f"{tag}:coarse")
        yc, width, e1, e2 = dip_center(ys, amps, max_ratio=0.75,
                                       max_width=max_width)
        info = {"y_coarse": _r(yc), "width": _r(width)}
        if fine:
            try:
                b2 = list(base)
                b2[axis] = yc
                fy = self._fine_dip(b2, e1, e2, label, 1, tag=f"{tag}:fine",
                                    axis=axis, max_width=max_width)
                yc = fy["center"]
                info.update({"y": _r(yc), "y_err": _r(fy["err"]),
                             "width": _r(fy["width"]),
                             "sigma": _r(fy["sigma"])})
            except ValueError as exc:
                self._warnings.append(f"{label}: 精密仕上げ不可 ({exc}) → "
                                      "粗検出値")
        return yc, e1, e2, info

    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_bl(v) -> str:
        return "n/a" if v is None or not np.isfinite(v) else f"{v:+.1f} µm"

    def _fine_summary(self, fy, fx, fz, verify, cfg) -> str:
        pixel = str(self.p.get("sensor_type", "strip")) == "pixel"
        lines = [f"精密仕上げ (1 パルス刻み・{cfg['reads']} 回読み中央値・"
                 "erf / ローレンツ型フィット):"]
        if fy:
            lines.append(
                f"  Y {'パッド中心' if pixel else '電極中心'}: フィット誤差 ±{fy['err']:.2f} µm, "
                f"{fy['n_pass']} 回の差 {fy['spread']:.2f} µm, "
                f"エッジ幅 σ={fy['sigma']:.1f} µm, "
                f"逆方向との差 (バックラッシュ推定) "
                f"{self._fmt_bl(fy.get('backlash'))}")
        if fx:
            lines.append(
                f"  X {'パッド中心' if pixel else '信号エッジ'}: ±{fx['err']:.2f} µm, "
                f"{fx['n_pass']} 回の差 {fx['spread']:.2f} µm, "
                f"σ={fx['sigma']:.1f} µm, バックラッシュ推定 "
                f"{self._fmt_bl(fx.get('backlash'))}")
        if fz and fz.get("method") == "fit":
            lines.append(
                f"  Z 焦点: 谷の深さ {fz.get('depth_pct', 0):.0f} %, "
                f"γ={fz.get('gamma', 0):.0f} µm, "
                f"{len(fz.get('per_pass', []))} 回の差 "
                f"{fz.get('spread', 0):.2f} µm (丸め前 {fz.get('center', 0):+.1f})")
        if verify:
            rx, ry, rz = verify["residual_um"]
            corr = verify["corrections_um"]
            ctxt = ("補正なし" if not corr else
                    "補正 " + "; ".join(
                        f"X={c[0]:+.0f} Y={c[1]:+.0f} Z={c[2]:+.0f}"
                        for c in corr) + " µm")
            ok = all(abs(v) < 1.0 for v in (rx, ry, rz))
            lines.append(
                f"検証 (R:W 後に再測定, {verify['n_measure']} 回, {ctxt}): "
                f"残差 X={rx:+.1f} Y={ry:+.1f} Z={rz:+.1f} µm "
                + ("→ 1 パルス (2 µm) 未満 OK" if ok else "→ ★1 パルス以上★"))
        return "\n".join(lines)


class _Aborted(Exception):
    pass
