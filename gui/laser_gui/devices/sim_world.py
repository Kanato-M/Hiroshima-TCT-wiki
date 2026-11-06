# -*- coding: utf-8 -*-
"""--simulate 用の疑似センサー応答 (ステージ位置・レーザー状態 → 振幅)。

シミュレーションモードでオシロの振幅読み出し (PAVA) を一定値のままに
すると、自動アライメント・Z フォーカス・TUNE 最適化の動作確認ができない
(ディップもエッジも谷も出ない)。そこでステージの「真の機械座標」と
レーザー状態から、実機で観測される振る舞いを単純化して再現する:

  - 金属電極の直上では信号がゼロ (ビーム径で切れたエッジ)
  - ストリップ露出部の外 (ボンドパッド側・反対側の端より先) では信号なし
  - 読み出し CH の電極から離れるほど振幅が指数的に減る (電荷共有)
  - 合焦するほど振幅が小さくなる (LGAD ゲイン抑制の浅い谷)、
    デフォーカスするとビーム径が広がり電極エッジがなまる
  - センサーは傾いて置かれている (フォーカス面チルト + 面内回転)
  - TUNE を下げるほど振幅が増え、発振閾値より上 (≳97%) では光が出ない

数値は実機の桁に合わせた仮のもので、物理モデルとしての正確さは
意図していない (GUI のシーケンスを最後まで通すためのもの)。
"""

from __future__ import annotations

import math
import random


class SimWorld:
    # 疑似センサーの置き方 (機械座標 [µm] と傾き [µm/µm])
    ORIGIN_UM = (108932.0, 100050.0, 1414.0)   # 外枠エッジ・基準電極・合焦 Z
    DZDX, DZDY, DYDX = 0.0003, 0.0045, 0.0075
    SHARE_LAMBDA_UM = 300.0     # 電荷共有の減衰長
    BEAM_R0_UM = 3.0            # 合焦時のビーム半径
    NOISE_V = 0.0015

    def __init__(self, stage, laser, registry):
        self.stage = stage
        self.laser = laser
        self.registry = registry

    def _machine_pos(self) -> list[float]:
        return [self.stage.pulse_to_um(i, p)
                for i, p in enumerate(self.stage._sim_abs)]

    def _laser_amplitude(self) -> float:
        """レーザー状態から決まる基準振幅 [V] (TUNE 90%→0.1, 50%→0.21)。"""
        st = self.laser._sim
        if not st["emission_pulsed"]:
            return 0.0
        tune = st["tune_permil"] / 100.0
        if st["tune_mode"]:          # auto はコントローラの校正値 (中庸)
            tune = 70.0
        if tune > 97.0:              # 発振閾値未満
            return 0.0
        return 0.1 + (90.0 - tune) * 0.00275

    def amplitude(self, ch: str) -> float:
        """現在のステージ位置での CH の PKPK 振幅 [V]。"""
        prof = self.registry.current()
        x, y, z = (p - o for p, o in zip(self._machine_pos(),
                                         self.ORIGIN_UM))
        x_off = float(prof["x_frame_offset_um"])
        length = float(prof["strip_length_um"])
        width = float(prof["electrode_width_um"])
        ro = prof["readout"]
        y_ref = float(ro[prof["ref_channel"]]["y_um"])
        pitch = float(prof["pitch_um"])

        # 合焦面からのずれ → ビーム半径とゲイン抑制
        dz = z - (self.DZDX * x + self.DZDY * y)
        r = math.hypot(self.BEAM_R0_UM, 0.25 * dz)
        gain = 1.0 - 0.35 * math.exp(-(dz / 10.0) ** 2)

        # ストリップ露出部 (x = −x_off 〜 −(x_off+length)) の内側だけ信号
        def step(d):   # ビーム径でなまったステップ (d>0 で 1)
            return 0.5 * (1.0 + math.erf(d / (r * math.sqrt(2.0))))
        exposed = step(-x_off - x) * step(x + x_off + length)

        # 電極 (回転した直線) の金属による遮蔽。読み出し外の両隣も置く
        y_line = y - self.DYDX * (x + x_off)
        centers = {c: float(v["y_um"]) - y_ref for c, v in ro.items()}
        metals = list(centers.values())
        metals += [max(metals) + pitch, min(metals) - pitch]
        open_frac = 1.0
        for yc in metals:
            d = y_line - yc
            open_frac -= step(d + width / 2.0) - step(d - width / 2.0)
        open_frac = max(open_frac, 0.0)

        share = math.exp(-abs(y_line - centers.get(ch, 0.0))
                         / self.SHARE_LAMBDA_UM)
        amp = self._laser_amplitude() * gain * exposed * open_frac * share
        return max(amp + random.gauss(0.0, self.NOISE_V), 0.0)
