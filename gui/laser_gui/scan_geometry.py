# -*- coding: utf-8 -*-
"""センサー傾き (チルト・回転) の実測マップと補正係数 (2026-08-24 実測)。

セットアップ治具の改良 (2026-08) に伴い、ステージ位置を再実測した。
今回は各ストリップ中心の位置を **論理座標 [µm] (論理原点 = C1 の X=0)** で
記録し、機械絶対座標は論理原点の機械座標 MACHINE_ORIGIN_UM を足して
再構成する (Z はカメラ焦点が合う高さ)。読み出しストリップは C1–C4 で、
C0/C5 はその外側の (読み出さない) 隣接ストリップ。

X 座標の定義 (2026-09-07 更新, "Strip detail" スライド参照):
  - **X=0 はセンサー外枠 (GND) エッジ** (2026-09-07 定義変更。それまでは
    ワイヤボンドパッド領域②とスキャン領域③の境界 = ストリップ露出部の
    読み出し側端を X=0 としていた)。
  - 外枠エッジからの内訳 (スライド実測):
      ⓪ 0.052 mm (外枠エッジ〜) ① 0.380 mm ② 0.200 mm (ボンドパッド)
      ③ 9.318 mm (ストリップ露出 = スキャン領域) ④ 0.098 mm (端ギャップ)
    ⓪+①+② = 0.632 mm がストリップ露出開始、⓪+①+②+③+④/2 = 9.999 mm
    が 1 ストリップ分の範囲 → **原点から 10 mm 移動するとギャップ④内
    (2 本のストリップ領域の間) に来る**。
  - **物理 X+ 方向はステージ x− 方向** (外枠エッジが機械座標 x 大)。
    論理座標ではストリップ露出部が x = −632 〜 −9950、ギャップ④中央が
    x = −9999 になる。

実測から分かったセンサーの傾き (STRIP_POINTS への最小二乗フィット。
係数は差分ベースなので論理/機械どちらの座標でも同値):
  1. フォーカス面のチルト: dZ/dX = −0.02 µm/mm (旧治具 +1.04 からほぼ
     平坦化), dZ/dY = +3.79 µm/mm (残差 最大 ±1.6 µm ≦ Z 分解能
     2 µm/pulse)。X 方向はほぼフラットだが Y 方向はスキャン幅 3 mm で
     Z が ~11 µm 変わるため、ビーム半径数 µm を保つには Z 追従が必要。
  2. XY 面内の回転: ストリップ中心の Y が X に沿って +7.7 µm/mm
     ドリフトする (ストリップ全長で ~72 µm、回転角 ~0.44°)。

センサーを置き直して平行を出すより、スキャン側でこの傾きを補正する方が
簡単 (2026-07-06 からの方針)。絶対座標は M-ORG のたびに変わり得るが、
傾きの係数 (勾配) は原点の取り方に依存しないため、論理原点をどこに
リセットしても同じ係数が使える。スキャン自体は論理原点基準の相対移動
(M:W) で行う。

注意:
- 座標系の確認: 当日ログ gui_20260824.log の R:W 直前のステージ位置
  (54150, 50025, 707) pulse は 2 µm/pulse 換算で (108300, 100050, 1414) µm
  = MACHINE_ORIGIN_UM と一致しており、この表は現行 GUI 表示系
  (2 µm/pulse 校正) の µm で正しい。
"""

from __future__ import annotations

MEASURED_DATE = "2026-08-24"

# ------------------------------------------------------------------
# ストリップ長手 (X) 方向の設計寸法 [µm] ("Strip detail" スライド実測)
# ------------------------------------------------------------------
SEG_FRAME_UM = 52.0     # ⓪ 外枠 (GND) エッジ〜
SEG_RING_UM = 380.0     # ①
SEG_PAD_UM = 200.0      # ② ワイヤボンドパッド領域
SEG_STRIP_UM = 9318.0   # ③ ストリップ露出部 (スキャン領域)
SEG_GAP_UM = 98.0       # ④ ストリップ端と隣接領域のギャップ
# 外枠エッジ (X=0) → ストリップ露出開始 (②/③境界) のオフセット
X_FRAME_OFFSET_UM = SEG_FRAME_UM + SEG_RING_UM + SEG_PAD_UM   # 632
# 1 ストリップ分の X 範囲 (外枠エッジ→ギャップ④中央): 9999 µm ≈ 10 mm
X_STRIP_UNIT_UM = X_FRAME_OFFSET_UM + SEG_STRIP_UM + SEG_GAP_UM / 2

# 論理原点 (外枠エッジ, C1 電極中心 Y) の機械座標 [µm]
# (2026-08-24 R:W 時の実測は旧定義 (②/③境界) だったため +632 µm 換算)。
MACHINE_ORIGIN_UM: tuple[float, float, float] = (
    108300.0 + X_FRAME_OFFSET_UM, 100050.0, 1414.0)

# ストリップ中心の実測論理座標 [µm]。実測 (2026-08-24) は旧定義
# (X=0 = ②/③境界) で記録したため、X から X_FRAME_OFFSET_UM を引いて
# 現行定義 (X=0 = 外枠エッジ) に変換する。
_STRIP_POINTS_SCAN_EDGE: dict[str, tuple[tuple[float, float, float], ...]] = {
    "C0": ((-4.0, 500.0, 2.0), (-4504.0, 464.0, 0.0)),
    "C1": ((0.0, 0.0, 0.0), (-4500.0, -36.0, 0.0),
           (-9318.0, -70.0, 0.0), (-9370.0, -72.0, 0.0)),
    "C2": ((6.0, -500.0, -2.0), (-4494.0, -536.0, -2.0)),
    "C3": ((10.0, -1000.0, -4.0), (-4490.0, -1036.0, -4.0)),
    "C4": ((14.0, -1500.0, -6.0), (-4486.0, -1536.0, -6.0)),
    "C5": ((18.0, -2000.0, -8.0), (-4482.0, -2036.0, -8.0),
           (-9300.0, -2070.0, -8.0)),
}
STRIP_POINTS_LOGICAL: dict[str, tuple[tuple[float, float, float], ...]] = {
    name: tuple((p[0] - X_FRAME_OFFSET_UM, p[1], p[2]) for p in pts)
    for name, pts in _STRIP_POINTS_SCAN_EDGE.items()
}

# ストリップ中心の機械絶対座標 [µm] (= 機械原点 + 論理オフセット)。
# ブックマーク (A:W 絶対移動) 用。M-ORG のたびに変わり得るため目安。
STRIP_POINTS: dict[str, tuple[tuple[float, float, float], ...]] = {
    name: tuple(tuple(o + d for o, d in zip(MACHINE_ORIGIN_UM, p))
                for p in pts)
    for name, pts in STRIP_POINTS_LOGICAL.items()
}

# ストリップピッチ [µm] (C0端→C5端 の Y 実測差 / 5 = 500 ちょうど)
STRIP_PITCH_UM = (STRIP_POINTS["C0"][0][1] - STRIP_POINTS["C5"][0][1]) / 5.0

# 自動スキャンの既定範囲 (論理原点 = 外枠エッジ・C1電極中心 Y):
#   高速軸 Y: C0 中心 (+500) と C5 中心 (−2000) の外側に半ピッチ
#             (250 µm) の余裕を取り、外側ストリップの電極まで横断する
#   低速軸 X: ストリップ露出部 ③ (−632 〜 −9950) + ④/2 をカバーする
#             (−9999 ≈ −10 mm がギャップ④中央 = ストリップ領域の境界)
SCAN_FAST_Y_START = 750.0
SCAN_FAST_Y_STOP = -2250.0
SCAN_FAST_Y_STEP = 10.0
SCAN_SLOW_X_START = -X_FRAME_OFFSET_UM              # -632
SCAN_SLOW_X_STOP = -round(X_STRIP_UNIT_UM)          # -9999
SCAN_SLOW_X_STEP = 500.0


def _fit_tilt_plane(points) -> tuple[float, float, float]:
    """z = dzdx·x + dzdy·y + z0 の最小二乗フィット。

    数値安定のため重心まわりに中心化して 2×2 の正規方程式を解く
    (中心化すると切片項が消える)。
    """
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    sxx = sum((p[0] - cx) ** 2 for p in points)
    syy = sum((p[1] - cy) ** 2 for p in points)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in points)
    sxz = sum((p[0] - cx) * (p[2] - cz) for p in points)
    syz = sum((p[1] - cy) * (p[2] - cz) for p in points)
    det = sxx * syy - sxy * sxy
    dzdx = (sxz * syy - syz * sxy) / det
    dzdy = (syz * sxx - sxz * sxy) / det
    return dzdx, dzdy, cz - dzdx * cx - dzdy * cy


def _fit_rotation(strips) -> float:
    """各ストリップの Y(X) 直線の共通傾き dy/dx を最小二乗で求める。

    ストリップごとの切片 (Y 位置) は自由、傾きだけ共通とする。
    ストリップごとの点数は可変 (2 点以上) でよい。
    """
    num = den = 0.0
    for pts in strips:
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        num += sum((p[0] - mx) * (p[1] - my) for p in pts)
        den += sum((p[0] - mx) ** 2 for p in pts)
    return num / den


_ALL_POINTS = [p for pts in STRIP_POINTS.values() for p in pts]

# フォーカス面チルト [µm/µm]: dZ/dX=-2.14e-5, dZ/dY=+3.785e-3 (実測フィット)
TILT_DZDX, TILT_DZDY, _PLANE_Z0 = _fit_tilt_plane(_ALL_POINTS)
# 面内回転 [µm/µm]: dY/dX=+7.67e-3 (ストリップ全長 9.37 mm で ~72 µm)
ROT_DYDX = _fit_rotation(STRIP_POINTS.values())


def tilt_summary() -> str:
    """UI 表示用の係数サマリ文字列。"""
    return (f"dZ/dX={TILT_DZDX * 1000:+.2f}, dZ/dY={TILT_DZDY * 1000:+.2f}, "
            f"dY/dX={ROT_DYDX * 1000:+.1f} µm/mm ({MEASURED_DATE} 実測フィット)")


def strip_y_offsets_um() -> dict[str, float]:
    """C1端を 0 とした各ストリップ中心 (端側) の Y オフセット [µm]。"""
    y1 = STRIP_POINTS["C1"][0][1]
    return {name: pts[0][1] - y1 for name, pts in STRIP_POINTS.items()}


def stage_presets() -> dict[str, list[float]]:
    """config.stage_presets 用: 実測絶対座標のブックマーク初期値。

    絶対座標は M-ORG のたびに変わるため「目安」であり、実測日を含めて
    表示する。実測位置へ大まかに移動 → カメラで微調整 → C1端で
    フォーカスを合わせて論理原点リセット、という使い方を想定。
    C1 のみ長手方向 4 点 (X=0 / 4.5 / 9.318 / 9.37 mm) を登録する。
    """
    presets = {
        f"論理原点: 外枠エッジ X=0 (C1電極Y) ({MEASURED_DATE}換算)":
            list(MACHINE_ORIGIN_UM),
        f"C1端 読み出し側 X=-632 ({MEASURED_DATE})":
            list(STRIP_POINTS["C1"][0]),
    }
    for name in ("C0", "C2", "C3", "C4", "C5"):
        presets[f"{name}端 読み出し側 X=-632 ({MEASURED_DATE})"] = \
            list(STRIP_POINTS[name][0])
    presets[f"C1中央 X=-5.1mm ({MEASURED_DATE})"] = list(STRIP_POINTS["C1"][1])
    presets[f"C1反対側 X=-9.95mm ストリップ端 ({MEASURED_DATE})"] = \
        list(STRIP_POINTS["C1"][2])
    presets[f"C1スキャン終端 X=-10.0mm ({MEASURED_DATE})"] = \
        list(STRIP_POINTS["C1"][3])
    return presets
