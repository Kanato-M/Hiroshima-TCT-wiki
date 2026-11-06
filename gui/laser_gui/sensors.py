# -*- coding: utf-8 -*-
"""センサー / DUT プロファイルの登録辞書 (2026-10 追加)。

ボードやセンサーを載せ替えるたびにコード (scan_geometry.py) を編集しなくて
済むよう、センサーごとに次の情報を 1 つのプロファイルにまとめて
<log_dir>/sensors.json に保存する:

  - 形状: ストリップピッチ・電極幅・外枠エッジ→露出開始のオフセット・
    ストリップ露出長・端ギャップ
  - 読み出し: オシロ CH → パッド名・電極中心 Y (基準 CH の電極中心を Y=0)
  - スキャン既定範囲 / 自動アライメントの測定点 (形状から自動導出も可)
  - 傾き補正係数 (フル較正・手動入力の結果。センサーごとに独立)
  - 論理原点の機械座標 (原点出しのたびに自動登録)
  - 登録位置 (名前 → 座標。座標系は "logical"=論理原点基準 /
    "machine"=機械原点基準)

座標の約束 (scan_geometry.py と同じ):
  論理原点 = センサー外枠 (GND) エッジ X=0・基準 CH の電極中心 Y=0・
  合焦 Z=0。物理 X+ はステージ x− なので、ストリップ露出部は
  x = −x_frame_offset 〜 −(x_frame_offset + strip_length)。

ピクセル型 (2026-10-16 追加、sensor_type = "pixel"):
  - 形状: ピクセルピッチ pitch_um・ボンディングパッド (正方形、無信号) の辺長 pad_um
  - 読み出し: オシロ CH → パッド名・ピクセルの (列 col, 行 row)。基準 CH が (0, 0)
  - 論理原点 = 基準ピクセルのパッドの +X/+Y 側の角 (2026-10-15 のスキャンと同じ:
    パッドは x, y ∈ [−pad, 0]、ピクセル中心 = パッド中心 = (−pad/2 − col·pitch, −pad/2 − row·pitch))。
    Z=0 は合焦。4 ピクセルの交点は (−pad/2 − pitch/2, −pad/2 − pitch/2)
  - 原点出し: パッド (無信号の正方形) の Y ディップ → パッド横のシリコン上で Z →
    X ディップ → パッドの角で R:W。フル較正は他の読み出しピクセルのパッド中心で
    同じ測定をして平面フィット (dZ/dX, dZ/dY) と行方向の dY/dX を求める
  - 既定スキャン: パッドの角 (0, 0) から、読み出しピクセルの反対側のパッド端まで
    (2×2 ピッチ 500・パッド 100 なら 600 × 600 µm、10 µm 刻み)

標準センサー (500 µm ピッチ・10 mm ストリップ) の既定値は scan_geometry.py
(2026-08-24 実測) から生成する。旧形式の logs/sensor_calibration.json と
logs/positions.json は初回起動時に標準センサーのプロファイルへ移行する。
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
from pathlib import Path

import scan_geometry

FILENAME = "sensors.json"
VERSION = 1
STD_ID = "strip500_std"
CHANNELS = ("C1", "C2", "C3", "C4")

SENSOR_TYPES = (("strip", "ストリップ"), ("pixel", "ピクセル"))
PIXEL_PLACES = (("パッド横 (信号あり)", "beside"), ("パッド中心 (無信号)", "center"),
                ("4 ピクセル交点", "corner"))
STRIP_PLACES = (("読み出し側端", "near"), ("中央", "mid"), ("反対側端", "far"))

_LEGACY_CALIB = "sensor_calibration.json"
_LEGACY_BOOKMARKS = "positions.json"
_LEGACY_BOOKMARK_MARKER = "_units_calibrated_2um_per_pulse"


# ----------------------------------------------------------------------
# 形状からの自動導出 (センサー編集ダイアログの「形状から自動設定」でも使う)
# ----------------------------------------------------------------------
def sensor_type(profile: dict) -> str:
    """"strip" (既定) / "pixel"。"""
    return str(profile.get("sensor_type", "strip") or "strip")


def is_pixel(profile: dict) -> bool:
    return sensor_type(profile) == "pixel"


def pixel_center(profile: dict, ch: str) -> tuple[float, float]:
    """ピクセル型: CH のピクセル中心 (= パッド中心) の論理座標 (x, y) [µm]。
    論理原点は基準ピクセルのパッドの +X/+Y 側の角。"""
    pad = float(profile.get("pad_um", 100.0))
    pitch = float(profile["pitch_um"])
    ro = profile["readout"].get(ch, {})
    col, row = int(ro.get("col", 0)), int(ro.get("row", 0))
    return (-pad / 2.0 - col * pitch, -pad / 2.0 - row * pitch)


def channel_xy(profile: dict, ch: str) -> tuple[float, float]:
    """CH の電極の代表座標 (ピクセル: ピクセル中心、ストリップ: (0, 電極中心 Y))。"""
    if is_pixel(profile):
        return pixel_center(profile, ch)
    ro = profile["readout"]
    y_ref = float(ro.get(profile.get("ref_channel", "C1"), {}).get("y_um", 0.0))
    return (0.0, float(ro[ch]["y_um"]) - y_ref)


def used_channels(profile: dict) -> list[str]:
    """読み出しに使う CH を基準 CH からの距離順 (基準 CH が先頭) で返す。"""
    ro = profile.get("readout", {})
    ref = profile.get("ref_channel", "C1")
    chans = [ch for ch in CHANNELS if ro.get(ch, {}).get("use", True)
             and ch in ro]
    if is_pixel(profile):
        x0, y0 = pixel_center(profile, ref) if ref in ro else (0.0, 0.0)
        return sorted(chans, key=lambda ch: (
            ch != ref, abs(pixel_center(profile, ch)[0] - x0)
            + abs(pixel_center(profile, ch)[1] - y0), ch))
    y_ref = float(ro.get(ref, {}).get("y_um", 0.0))
    return sorted(chans, key=lambda ch: (ch != ref,
                                         abs(ro[ch]["y_um"] - y_ref)))


def derive_scan(profile: dict) -> dict:
    """形状からスキャン既定範囲を導出する。

    高速軸 Y: 読み出しストリップの外側に 1.5 ピッチ (隣の非読み出し
              ストリップ + 半ピッチ) の余裕を取って横断する
    低速軸 X: ストリップ露出部の読み出し側端 → 反対側の端ギャップ中央
    """
    ro = profile["readout"]
    if is_pixel(profile):
        # パッドの角 (0, 0) から、読み出しピクセルの反対側のパッド端まで (10 µm 刻み)
        pad = float(profile.get("pad_um", 100.0))
        cs = [pixel_center(profile, ch) for ch in used_channels(profile)] or [(-pad / 2, -pad / 2)]
        return {
            "fast_start": 0.0,
            "fast_stop": float(min(c[1] for c in cs) - pad / 2.0),
            "fast_step": 10.0,
            "slow_start": 0.0,
            "slow_stop": float(min(c[0] for c in cs) - pad / 2.0),
            "slow_step": 10.0,
        }
    ys = [ro[ch]["y_um"] for ch in used_channels(profile)] or [0.0]
    pitch = float(profile["pitch_um"])
    x_off = float(profile["x_frame_offset_um"])
    x_end = x_off + float(profile["strip_length_um"]) \
        + float(profile.get("x_end_gap_um", 0.0)) / 2.0
    return {
        "fast_start": max(ys) + 1.5 * pitch,
        "fast_stop": min(ys) - 1.5 * pitch,
        "fast_step": 10.0,
        "slow_start": -x_off,
        "slow_stop": -float(round(x_end)),
        "slow_step": 500.0,
    }


def derive_align(profile: dict) -> dict:
    """形状から自動アライメント (フル較正) の測定点を導出する。

    X 測定点: ストリップ露出部の中央付近と反対側端の 50 µm 手前
              (回転 dY/dX とチルト dZ/dX のレバーアームを最大にする)
    Y 測定点: 基準 CH 以外の読み出し電極のうち最も近いものと最も遠いもの
              (その電極を読む CH で振幅を測る。dZ/dY とピッチ確認用)
    """
    ro = profile["readout"]
    ref = profile.get("ref_channel", "C1")
    if is_pixel(profile):
        # 較正点 = 基準以外の読み出しピクセルのパッド中心 (その CH で振幅を読む)。
        # Z は パッド端 + 80 µm のシリコン上 (ピクセル内、信号が明瞭)。
        pad = float(profile.get("pad_um", 100.0))
        pads = [[ch, *pixel_center(profile, ch)]
                for ch in used_channels(profile) if ch != ref]
        return {
            "x_points": [], "y_points": [], "pads": pads,
            "y_probe_off_um": pad / 2.0 + 80.0,
            "x_probe_off_um": 0.0,
            "y_half_um": pad + 100.0,
        }
    y_ref = float(ro.get(ref, {}).get("y_um", 0.0))
    x_off = float(profile["x_frame_offset_um"])
    length = float(profile["strip_length_um"])
    pitch = float(profile["pitch_um"])
    x_mid = -round((x_off + 0.5 * length) / 100.0) * 100.0
    x_far = -(x_off + length - 50.0)
    others = [ch for ch in used_channels(profile) if ch != ref]
    y_points = []
    if others:
        picks = [others[0]] if len(others) == 1 else [others[0], others[-1]]
        y_points = [[ro[ch]["y_um"] - y_ref, ch] for ch in picks]
    return {
        "x_points": [x_mid, x_far],
        "y_points": y_points,
        # Z フォーカスは電極中心から y_probe_off 横のシリコン上で行う
        # (電極半幅 + デフォーカス時のスポット拡がりの余裕)。狭ピッチでは
        # 隣の電極にかからないようピッチの 1/3 以内に抑える
        "y_probe_off_um": min(80.0, round(pitch / 3.0)),
        # X エッジ検出はボンドパッドの Y 幅に収まる電極寄りで走査する
        "x_probe_off_um": min(50.0, round(pitch / 4.0)),
        "y_half_um": min(150.0, round(pitch * 0.3)),
    }


def _std_profile() -> dict:
    """標準センサー (scan_geometry.py の 2026-08-24 実測) のプロファイル。"""
    g = scan_geometry
    p = {
        "id": STD_ID,
        "name": "標準ストリップ (500 µm ピッチ, 10 mm)",
        "note": f"scan_geometry.py ({g.MEASURED_DATE} 実測) 由来の既定センサー",
        "sensor_type": "strip",
        "pitch_um": float(g.STRIP_PITCH_UM),
        "pad_um": 100.0,
        "electrode_width_um": 50.0,
        "x_frame_offset_um": float(g.X_FRAME_OFFSET_UM),
        "strip_length_um": float(g.SEG_STRIP_UM),
        "x_end_gap_um": float(g.SEG_GAP_UM),
        "readout": {
            "C1": {"pad": "J8", "y_um": 0.0, "use": True},
            "C2": {"pad": "J7", "y_um": -500.0, "use": True},
            "C3": {"pad": "J6", "y_um": -1000.0, "use": True},
            "C4": {"pad": "J5", "y_um": -1500.0, "use": True},
        },
        "ref_channel": "C1",
        "calibration": None,
        "machine_origin": {
            "pos_um": list(g.MACHINE_ORIGIN_UM),
            "registered_at": g.MEASURED_DATE,
            "method": "scan_geometry 既定 (目安)",
        },
        "positions": {},
    }
    p["scan"] = derive_scan(p)
    p["align"] = derive_align(p)
    return p


def _template(sid: str, name: str, pitch: float, note: str) -> dict:
    """別 DUT 用のひな形 (標準センサーの寸法を流用しピッチだけ差し替え)。"""
    p = _std_profile()
    p.update({"id": sid, "name": name, "note": note, "pitch_um": pitch,
              "machine_origin": None})
    for i, ch in enumerate(CHANNELS):
        p["readout"][ch]["y_um"] = -pitch * i
    p["scan"] = derive_scan(p)
    p["align"] = derive_align(p)
    return p


# 2026-10-15 のピクセルスキャン (scan_20261015_193055_2dYX) で実測した読み出し配置:
# パッド (無信号の 100 µm 角) は C1 (−50, −50), C2 (−550, −50), C3 (−50, −550), C4 (−550, −550)
# → C1 (列 0, 行 0)、C2 (1, 0) = −X 側の隣、C3 (0, 1) = −Y 側の隣、C4 (1, 1) = 対角。4 ピクセルの交点 (−300, −300)
PIXEL_2X2_LAYOUT = {"C1": (0, 0), "C2": (1, 0), "C3": (0, 1), "C4": (1, 1)}


def pixel_profile(sid: str = "pixel_2x2_500", name: str = "ピクセル 2x2 (500 µm ピッチ, パッド 100 µm)",
                  pitch: float = 500.0, pad: float = 100.0, pads: dict | None = None,
                  note: str = "") -> dict:
    """ピクセル型 (2x2 読み出し) のプロファイルのひな形。"""
    pads = pads or {"C1": "J13", "C2": "J14", "C3": "J15", "C4": "J16"}
    p = {
        "id": sid, "name": name,
        "note": note or ("2026-10-15 の pixel#3 スキャンの配置 (パッドの角が原点、C2 が −X 隣、"
                         "C3 が −Y 隣)。ボードを変えたら列・行とパッド名を確認すること"),
        "sensor_type": "pixel",
        "pitch_um": float(pitch), "pad_um": float(pad),
        # ストリップ用の寸法は使わないが、旧コード互換のため標準値を入れておく
        "electrode_width_um": 50.0, "x_frame_offset_um": 0.0,
        "strip_length_um": float(pitch), "x_end_gap_um": 0.0,
        "readout": {}, "ref_channel": "C1",
        "calibration": None, "machine_origin": None, "positions": {},
    }
    for ch in CHANNELS:
        col, row = PIXEL_2X2_LAYOUT[ch]
        p["readout"][ch] = {"pad": pads.get(ch, ""), "use": True, "col": col, "row": row,
                            "y_um": -pad / 2.0 - row * pitch}
    p["scan"] = derive_scan(p)
    p["align"] = derive_align(p)
    return p


def builtin_profiles() -> dict[str, dict]:
    unverified = ("★寸法未確認★ ピッチのみ解析 config 由来、他は標準センサーの"
                  "値を流用。実測値に編集してから使うこと")
    profs = [
        _std_profile(),
        _template("4col_1000", "4col (1000 µm ピッチ)", 1000.0, unverified),
        _template("varpitch_500", "可変ピッチ (500 µm 部)", 500.0, unverified),
        pixel_profile(),
    ]
    return {p["id"]: p for p in profs}


def validate(profile: dict) -> None:
    """プロファイルの整合性を確認する (不正なら ValueError)。"""
    if not str(profile.get("name", "")).strip():
        raise ValueError("センサー名が空です")
    if sensor_type(profile) not in [t for t, _ in SENSOR_TYPES]:
        raise ValueError(f"不明なセンサー種類: {profile.get('sensor_type')!r}")
    if float(profile.get("pitch_um", 0.0)) <= 0:
        raise ValueError("pitch_um は正の値にしてください")
    ref = profile.get("ref_channel")
    ro = profile.get("readout", {})
    if ref not in ro or not ro[ref].get("use", True):
        raise ValueError("基準 CH は読み出しに使う CH から選んでください")
    if is_pixel(profile):
        pad = float(profile.get("pad_um", 0.0))
        if pad <= 0:
            raise ValueError("パッド辺長 pad_um は正の値にしてください")
        if pad >= float(profile["pitch_um"]):
            raise ValueError("パッド辺長がピクセルピッチ以上です")
        seen = {}
        for ch, entry in ro.items():
            if not entry.get("use", True):
                continue
            key = (int(entry.get("col", 0)), int(entry.get("row", 0)))
            if key in seen:
                raise ValueError(f"{ch} と {seen[key]} が同じピクセル (列 {key[0]}, 行 {key[1]}) です")
            seen[key] = ch
        if (int(ro[ref].get("col", 0)), int(ro[ref].get("row", 0))) != (0, 0):
            raise ValueError("基準 CH のピクセルは (列 0, 行 0) にしてください (原点 = そのパッドの角)")
    else:
        for key in ("electrode_width_um", "strip_length_um"):
            if float(profile.get(key, 0.0)) <= 0:
                raise ValueError(f"{key} は正の値にしてください")
        if float(profile.get("x_frame_offset_um", -1.0)) < 0:
            raise ValueError("x_frame_offset_um は 0 以上にしてください")
        if float(profile["electrode_width_um"]) >= float(profile["pitch_um"]):
            raise ValueError("電極幅がピッチ以上です")
    sc = profile.get("scan", {})
    for key in ("fast_step", "slow_step"):
        if float(sc.get(key, 0.0)) <= 0:
            raise ValueError("スキャン step は正の値にしてください")


# ----------------------------------------------------------------------
class SensorRegistry:
    """センサープロファイルの読み書きと「現在のセンサー」の管理。

    GUI スレッドからのみ操作する (ワーカースレッドは開始時に必要な値を
    params として受け取り、結果は GUI スレッドでここへ書き戻す)。
    変更は subscribe() した各パネルへ通知される。
    """

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / FILENAME
        self._listeners: list = []
        self.sensors: dict[str, dict] = {}
        self.current_id = STD_ID
        self._load()

    # ------------------------------------------------------------------
    # 永続化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.sensors = dict(data.get("sensors", {}))
                self.current_id = data.get("current", STD_ID)
            except Exception:
                # 壊れたファイルは退避して既定値で作り直す (上書きで失わない)
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.path.replace(
                    self.path.with_name(f"sensors_broken_{stamp}.json"))
                self.sensors = {}
        fresh = not self.sensors
        if fresh:
            self.sensors = builtin_profiles()
            self._migrate_legacy()
        # 欠けているキーを既定値で補う (手編集・旧版ファイル対策)
        migrated = False
        for sid, prof in self.sensors.items():
            prof.setdefault("id", sid)
            base = _std_profile()
            for key, value in base.items():
                if key in ("calibration", "machine_origin", "positions",
                           "scan", "align", "id", "name", "note",
                           "sensor_type", "pad_um"):     # 種類は下で移行する
                    continue
                prof.setdefault(key, copy.deepcopy(value))
            prof.setdefault("name", sid)
            prof.setdefault("note", "")
            if "sensor_type" not in prof:
                # 旧版で登録したピクセルセンサー (id / 名前が pixel...) はピクセル型に移行する
                # (列・行は 2026-10-15 のスキャンで実測した配置)
                if str(sid).lower().startswith("pixel") \
                        or "pixel" in str(prof.get("name", "")).lower():
                    prof["sensor_type"] = "pixel"
                    prof.setdefault("pad_um", 100.0)
                    for ch, (col, row) in PIXEL_2X2_LAYOUT.items():
                        entry = prof.setdefault("readout", {}).setdefault(
                            ch, {"pad": "", "use": True})
                        entry.setdefault("col", col)
                        entry.setdefault("row", row)
                        entry["y_um"] = -float(prof["pad_um"]) / 2.0 \
                            - row * float(prof["pitch_um"])
                    prof["scan"] = derive_scan(prof)
                    prof["align"] = derive_align(prof)
                    prof["note"] = (str(prof.get("note", "")) + " [2026-10-16 ピクセル型へ自動移行: "
                                    "C2 = −X 隣, C3 = −Y 隣, 原点 = C1 パッドの角]").strip()
                else:
                    prof["sensor_type"] = "strip"
                migrated = True
            prof.setdefault("pad_um", 100.0)
            for ch in CHANNELS:
                if ch in prof.get("readout", {}):
                    prof["readout"][ch].setdefault("col", 0)
                    prof["readout"][ch].setdefault("row", 0)
            prof.setdefault("calibration", None)
            prof.setdefault("machine_origin", None)
            prof.setdefault("positions", {})
            prof.setdefault("scan", derive_scan(prof))
            prof.setdefault("align", derive_align(prof))
        # ピクセル型のひな形が無ければ追加する (旧版の sensors.json)
        if not any(is_pixel(pp) for pp in self.sensors.values()):
            tpl = pixel_profile()
            self.sensors[tpl["id"]] = tpl
            migrated = True
        if self.current_id not in self.sensors:
            self.current_id = next(iter(self.sensors))
        if fresh or migrated:
            self.save()

    def _migrate_legacy(self) -> None:
        """旧形式 (センサー共通の 1 ファイル) を標準センサーへ取り込む。"""
        std = self.sensors[STD_ID]
        cal_path = self.state_dir / _LEGACY_CALIB
        if cal_path.exists():
            try:
                cal = json.loads(cal_path.read_text(encoding="utf-8"))
                if all(k in cal for k in ("dzdx", "dzdy", "dydx")):
                    std["calibration"] = cal
            except Exception:
                pass
        bm_path = self.state_dir / _LEGACY_BOOKMARKS
        if bm_path.exists():
            try:
                marks = json.loads(bm_path.read_text(encoding="utf-8"))
                marks.pop(_LEGACY_BOOKMARK_MARKER, None)
                presets = scan_geometry.stage_presets()
                for name, pos in marks.items():
                    # scan_geometry 由来のプリセットは「ストリップへ移動」
                    # (形状 + 較正から都度計算) に置き換わるので移行しない。
                    # ユーザーが登録したものだけ機械座標として引き継ぐ
                    if name in presets:
                        continue
                    std["positions"][name] = {
                        "pos_um": [float(v) for v in pos],
                        "frame": "machine"}
            except Exception:
                pass

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": VERSION, "current": self.current_id,
                "sensors": self.sensors}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)   # 書き込み途中の電源断で壊さない

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------
    def subscribe(self, callback) -> None:
        self._listeners.append(callback)

    def notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------------------
    # センサーの選択・編集
    # ------------------------------------------------------------------
    def items(self) -> list[tuple[str, str]]:
        return [(sid, p.get("name", sid)) for sid, p in self.sensors.items()]

    def current(self) -> dict:
        return self.sensors[self.current_id]

    def select(self, sid: str) -> None:
        if sid in self.sensors and sid != self.current_id:
            self.current_id = sid
            self.save()
            self.notify()

    def new_id(self, name: str) -> str:
        base = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower() \
            or "sensor"
        sid, n = base, 2
        while sid in self.sensors:
            sid, n = f"{base}_{n}", n + 1
        return sid

    def upsert(self, profile: dict, select: bool = False) -> None:
        validate(profile)
        self.sensors[profile["id"]] = profile
        if select:
            self.current_id = profile["id"]
        self.save()
        self.notify()

    def remove(self, sid: str) -> None:
        if len(self.sensors) <= 1:
            raise ValueError("最後の 1 件は削除できません")
        self.sensors.pop(sid, None)
        if self.current_id == sid:
            self.current_id = next(iter(self.sensors))
        self.save()
        self.notify()

    # ------------------------------------------------------------------
    # 傾き補正係数
    # ------------------------------------------------------------------
    def coefficients(self, sid: str | None = None
                     ) -> tuple[float, float, float, str]:
        """スキャンで使う (dzdx, dzdy, dydx) [µm/µm] と出典ラベル。

        優先順位: プロファイルの calibration (フル較正 / 手動入力)
          → 標準センサーのみ scan_geometry.py のカメラ実測フィット
          → それ以外は未較正 (0, 0, 0)
        """
        prof = self.sensors[sid or self.current_id]
        cal = prof.get("calibration")
        if cal and all(k in cal for k in ("dzdx", "dzdy", "dydx")):
            date = str(cal.get("measured_at", ""))[:10]
            method = str(cal.get("method", ""))
            label = "手動入力" if ("manual" in method or "手動" in method) \
                else "フル較正"
            return (float(cal["dzdx"]), float(cal["dzdy"]),
                    float(cal["dydx"]), f"{label} {date}")
        if prof["id"] == STD_ID:
            g = scan_geometry
            return (g.TILT_DZDX, g.TILT_DZDY, g.ROT_DYDX,
                    f"scan_geometry 既定 ({g.MEASURED_DATE} カメラ実測)")
        return 0.0, 0.0, 0.0, "未較正 (補正量 0)"

    def summary(self, sid: str | None = None) -> str:
        dzdx, dzdy, dydx, source = self.coefficients(sid)
        return (f"dZ/dX={dzdx * 1000:+.2f}, dZ/dY={dzdy * 1000:+.2f}, "
                f"dY/dX={dydx * 1000:+.2f} µm/mm [{source}]")

    def rotation_ref_x(self, sid: str | None = None) -> float:
        """電極中心 Y=0 を実測した X (原点出しの位置)。回転の基準点。"""
        prof = self.sensors[sid or self.current_id]
        cal = prof.get("calibration") or {}
        if is_pixel(prof):
            default = -float(prof.get("pad_um", 100.0)) / 2.0     # 基準パッドの中心 X
        else:
            default = -(float(prof["x_frame_offset_um"]) + 60.0)
        return float(cal.get("x_yc_measured_um", default))

    def set_calibration(self, data: dict, sid: str | None = None) -> Path:
        """較正結果を保存する (履歴ファイルも残す)。"""
        sid = sid or self.current_id
        data = dict(data)
        data.setdefault("measured_at", datetime.datetime.now().isoformat())
        data["sensor_id"] = sid
        self.sensors[sid]["calibration"] = data
        self.save()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        hist = self.state_dir / f"sensor_calibration_{sid}_{stamp}.json"
        hist.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        self.notify()
        return hist

    def set_manual_coefficients(self, dzdx: float, dzdy: float,
                                dydx: float) -> Path:
        prev = self.coefficients()
        return self.set_calibration({
            "method": "manual (手動入力)",
            "dzdx": dzdx, "dzdy": dzdy, "dydx": dydx,
            "previous": {"dzdx": prev[0], "dzdy": prev[1],
                         "dydx": prev[2], "source": prev[3]}})

    # ------------------------------------------------------------------
    # 論理原点の機械座標 / 登録位置
    # ------------------------------------------------------------------
    def set_machine_origin(self, pos_um, method: str,
                           sid: str | None = None) -> None:
        self.sensors[sid or self.current_id]["machine_origin"] = {
            "pos_um": [float(v) for v in pos_um],
            "registered_at": datetime.datetime.now().isoformat(
                timespec="seconds"),
            "method": method,
        }
        self.save()
        self.notify()

    def positions(self, sid: str | None = None) -> dict:
        return self.sensors[sid or self.current_id]["positions"]

    def set_position(self, name: str, pos_um, frame: str) -> None:
        if frame not in ("logical", "machine"):
            raise ValueError(f"不明な座標系: {frame}")
        self.positions()[name] = {"pos_um": [float(v) for v in pos_um],
                                  "frame": frame}
        self.save()
        self.notify()

    def delete_position(self, name: str) -> None:
        self.positions().pop(name, None)
        self.save()
        self.notify()

    # ------------------------------------------------------------------
    # 形状ベースの位置計算 (「ストリップへ移動」用)
    # ------------------------------------------------------------------
    def places(self) -> list[tuple[str, str]]:
        """「電極へ移動」の選択肢 (表示名, キー)。センサー種類で変わる。"""
        return list(PIXEL_PLACES if is_pixel(self.current()) else STRIP_PLACES)

    def position_for(self, ch: str, where: str) -> list[float]:
        """CH の電極の論理座標 [µm] (ストリップ: strip_position、ピクセル: pixel_position)。"""
        if is_pixel(self.current()):
            return self.pixel_position(ch, where)
        return self.strip_position(ch, where)

    def pixel_position(self, ch: str, where: str) -> list[float]:
        """ピクセル型: CH のピクセルに関する論理座標 [µm]。

        where: "center" (パッド中心 = ピクセル中心、無信号) / "beside" (パッド端から
        probe_off 離れたシリコン上、信号が明瞭) / "corner" (4 ピクセルの交点のうち
        読み出しピクセル群の中心に近いもの)。Y は回転、Z はチルトを現在の係数で補正する。
        """
        prof = self.current()
        dzdx, dzdy, dydx, _ = self.coefficients()
        pad = float(prof.get("pad_um", 100.0))
        pitch = float(prof["pitch_um"])
        probe = float(prof.get("align", {}).get("y_probe_off_um", pad / 2.0 + 80.0))
        cx, cy = pixel_center(prof, ch)
        if where == "beside":
            x, y = cx, cy - probe
        elif where == "corner":
            cs = [pixel_center(prof, c) for c in used_channels(prof)]
            mx = sum(c[0] for c in cs) / len(cs)
            my = sum(c[1] for c in cs) / len(cs)
            sx = 1.0 if mx > cx else -1.0
            sy = 1.0 if my > cy else -1.0
            x, y = cx + sx * pitch / 2.0, cy + sy * pitch / 2.0
        else:
            x, y = cx, cy
        y = y + dydx * (x - self.rotation_ref_x())
        return [x, y, dzdx * x + dzdy * y]

    def strip_position(self, ch: str, where: str) -> list[float]:
        """CH の電極中心線上の論理座標 [µm] を返す。

        where: "near" (読み出し側端の 100 µm 内側) / "mid" (中央) /
        "far" (反対側端の 100 µm 内側)。Y は回転、Z はチルトを現在の
        係数で補正する (論理原点で合焦 Z=0 の前提)。
        """
        prof = self.current()
        dzdx, dzdy, dydx, _ = self.coefficients()
        x_off = float(prof["x_frame_offset_um"])
        length = float(prof["strip_length_um"])
        inset = min(100.0, 0.1 * length)
        depth = {"near": inset, "mid": 0.5 * length,
                 "far": length - inset}[where]
        x = -(x_off + depth)
        ro = prof["readout"]
        y_ref = float(ro[prof["ref_channel"]]["y_um"])
        y = float(ro[ch]["y_um"]) - y_ref \
            + dydx * (x - self.rotation_ref_x())
        return [x, y, dzdx * x + dzdy * y]

    def adjacent_pairs(self) -> list[tuple[str, str]]:
        """読み出し CH のうち電極が隣り合う組 (Y 順) を返す。

        TUNE 最適化の「隣接 2ch 合計」用。間に読み出さないストリップを
        挟む組 (|ΔY| がピッチの 1.5 倍以上) は隣接とみなさない。
        """
        prof = self.current()
        ro = prof["readout"]
        pitch = float(prof["pitch_um"])
        if is_pixel(prof):
            # 隣り合うピクセル (列または行が 1 違い)
            chans = used_channels(prof)
            pairs = []
            for i, a in enumerate(chans):
                for b in chans[i + 1:]:
                    da = abs(int(ro[a].get("col", 0)) - int(ro[b].get("col", 0)))
                    db = abs(int(ro[a].get("row", 0)) - int(ro[b].get("row", 0)))
                    if da + db == 1:
                        pairs.append((a, b))
            return pairs
        chans = sorted(used_channels(prof),
                       key=lambda c: -float(ro[c]["y_um"]))
        pairs = []
        for a, b in zip(chans, chans[1:]):
            if abs(float(ro[a]["y_um"]) - float(ro[b]["y_um"])) \
                    < 1.5 * pitch:
                pairs.append((a, b))
        return pairs

    def midpoint_position(self, ch_a: str, ch_b: str,
                          current_um) -> list[float]:
        """2 本の電極の中間線上で、現在の X に対応する論理座標 [µm]。

        Y は面内回転 (dY/dX) を現在の X で補正し、Z は現在の Z から
        チルト (dZ/dY) ぶんだけ相対的にずらす (現在位置で合焦している前提。
        論理原点基準の絶対 Z を使わないので、手動で Z を触った後でも
        焦点を保ったまま横へ動ける)。
        """
        prof = self.current()
        dzdx, dzdy, dydx, _ = self.coefficients()
        x, y_cur, z_cur = (float(v) for v in current_um)
        ro = prof["readout"]
        if is_pixel(prof):
            # 2 ピクセル中心の中点 (X も決まる)。Z は現在位置からの相対でチルト補正
            (xa, ya), (xb, yb) = pixel_center(prof, ch_a), pixel_center(prof, ch_b)
            x_mid, y_mid = 0.5 * (xa + xb), 0.5 * (ya + yb)
            y_mid += dydx * (x_mid - self.rotation_ref_x())
            return [x_mid, y_mid, z_cur + dzdx * (x_mid - x) + dzdy * (y_mid - y_cur)]
        y_ref = float(ro[prof["ref_channel"]]["y_um"])
        y_mid = 0.5 * (float(ro[ch_a]["y_um"]) + float(ro[ch_b]["y_um"])) \
            - y_ref + dydx * (x - self.rotation_ref_x())
        return [x, y_mid, z_cur + dzdy * (y_mid - y_cur)]
