# -*- coding: utf-8 -*-
"""スキャンフォルダ + GUI スキャンログ CSV の対応付けと共通メタデータ。

スキャンフォルダ名: scan_<YYYYMMDD_HHMMSS>_<タグ>
  タグ: 1dX/1dY/1dZ, 2dYX (高速軸・低速軸の順), tune, 1dYtune など
ログ CSV: laser_gui_v2\\logs\\scan_<YYYYMMDD_HHMMSS>.csv
  (# コメント行に mode / segments / 傾き補正係数 / base_pos を記録)

出力先の既定:
  ROOT ファイル → スキャンフォルダの隣 (<scan_dir>.root)
  解析結果      → <dataの親>\\results\\<スキャン名>\\
どちらも laser_gui_v2 の外に置く (英語版 GUI とデータを共有するため)。
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

AXIS_NAMES = ("X", "Y", "Z")
ALL_CHANNELS = ("C1", "C2", "C3", "C4")


def _workspace_root(scan_dir: Path) -> Path:
    """スキャンパスから 'data' ディレクトリの親 (ワークスペース) を探す。"""
    for parent in scan_dir.parents:
        if parent.name.lower() == "data":
            return parent.parent
    # フォールバック: このファイルの 2 つ上 (laser_gui_v2 の親)
    return Path(__file__).resolve().parents[2]


def _log_search_dirs(scan_dir: Path) -> list[Path]:
    ws = _workspace_root(scan_dir)
    here = Path(__file__).resolve().parents[1]  # laser_gui_v2
    return [scan_dir.parent / "logs",          # data\\<年>\\<日付>\\logs (データと一緒に回収した場合)
            scan_dir.parents[1] / "logs",
            here / "logs",
            ws / "laser_gui_v4" / "logs",
            ws / "laser_gui_v4_en" / "logs",
            ws / "laser_gui_v2" / "logs",
            ws / "laser_gui_en_v2" / "logs",
            ws / "laser_gui" / "logs",
            ws / "logs"]


@dataclass
class ScanInfo:
    scan_dir: Path
    name: str                      # scan_20260826_152932_2dYX
    stamp: str                     # 20260826_152932
    tag: str                       # 2dYX / 1dX / tune / 1dYtune
    mode: str                      # 1d / 2d / tune / 1d_tune
    prefix: str                    # 波形ファイルの接頭辞 (通常 "scan")
    channels: tuple[str, ...]      # 実在する CH のみ (例 ("C2","C3"))
    log_csv: Path | None
    header: dict = field(default_factory=dict)   # ログ # 行から抽出
    # 測定点配列 (ログ CSV の行順)
    index: np.ndarray = None       # int
    x_um: np.ndarray = None
    y_um: np.ndarray = None
    z_um: np.ndarray = None
    tune: np.ndarray = None        # NaN = TUNE スキャンでない
    ok: np.ndarray = None          # status == "ok"
    # 補正を外した公称座標 (グリッド化・軸表示用)
    xn: np.ndarray = None
    yn: np.ndarray = None
    zn: np.ndarray = None

    # ------------------------------------------------------------------
    @property
    def n_points(self) -> int:
        return len(self.index)

    def file_for(self, ch: str, idx: int) -> Path:
        p = self.scan_dir / f"{ch}--{self.prefix}--{idx:05d}.trc"
        if p.exists():
            return p
        # 旧オシロ連番形式や余分な連番サフィックス
        # (例 C2--scan--00042--00000.trc) へのフォールバック
        hits = sorted(self.scan_dir.glob(
            f"{ch}--{self.prefix}--{idx:05d}*.trc"))
        return hits[0] if hits else p

    def axis_letter(self, which: int = 0) -> str:
        """1d/1d_tune: スキャン軸。2d: which=0 高速軸, 1 低速軸。"""
        m = re.match(r"(1d|2d)([XYZ])([XYZ])?", self.tag)
        if m:
            letter = m.group(2) if which == 0 else (m.group(3) or m.group(2))
            return letter
        # タグから読めない場合は座標の分散が最大の軸
        spans = [np.ptp(v) for v in (self.xn, self.yn, self.zn)]
        order = np.argsort(spans)[::-1]
        return AXIS_NAMES[order[min(which, 2)]]

    def axis_values(self, letter: str) -> np.ndarray:
        return {"X": self.xn, "Y": self.yn, "Z": self.zn}[letter]

    def results_dir(self, results_root: Path | None = None) -> Path:
        root = Path(results_root) if results_root else \
            _workspace_root(self.scan_dir) / "results"
        return root / self.name

    def default_root_path(self) -> Path:
        return self.scan_dir.with_suffix(".root")

    def meta_json(self) -> str:
        return json.dumps({
            "scan_dir": str(self.scan_dir), "name": self.name,
            "stamp": self.stamp, "tag": self.tag, "mode": self.mode,
            "prefix": self.prefix, "channels": list(self.channels),
            "log_csv": str(self.log_csv), "header": self.header,
        }, ensure_ascii=False)


# ----------------------------------------------------------------------
def _parse_header_comments(lines: list[str]) -> dict:
    """ログ CSV の # コメント行から数値・文字列パラメータを抽出する。"""
    text = "\n".join(lines)
    out: dict = {"comments": lines}
    for key in ("mode", "prefix", "save_dir"):
        m = re.search(rf"{key}=(\S+)", text)
        if m:
            out[key] = m.group(1)
    for key in ("total_points", "segments"):
        m = re.search(rf"{key}=(\d+)", text)
        if m:
            out[key] = int(m.group(1))
    for key in ("dzdx", "dzdy", "dydx", "settle_s"):
        m = re.search(rf"{key}=([-+0-9.eE]+)", text)
        if m:
            out[key] = float(m.group(1))
    for key in ("tilt_correct", "rot_correct"):
        m = re.search(rf"{key}=(True|False)", text)
        if m:
            out[key] = m.group(1) == "True"
    m = re.search(r"base_pos_um=\(([^)]*)\)", text)
    if m and m.group(1).strip():
        out["base_pos_um"] = [float(v) for v in m.group(1).split(",")]
    return out


def _find_log_csv(scan_dir: Path, stamp: str) -> Path | None:
    for d in _log_search_dirs(scan_dir):
        p = d / f"scan_{stamp}.csv"
        if p.exists():
            return p
    return None


def _mode_from_tag(tag: str) -> str:
    if tag == "tune":
        return "tune"
    if tag.startswith("1d") and tag.endswith("tune"):
        return "1d_tune"
    if tag.startswith("2d"):
        return "2d"
    if tag.startswith("1d"):
        return "1d"
    return "1d"


def load_scan(scan_dir, log_csv=None) -> ScanInfo:
    """スキャンフォルダ (+ 任意でログ CSV) から ScanInfo を構築する。"""
    scan_dir = Path(scan_dir).resolve()
    if not scan_dir.is_dir():
        raise FileNotFoundError(f"スキャンフォルダがありません: {scan_dir}")
    name = scan_dir.name
    m = re.match(r"scan_(\d{8}_\d{6})_(.+)$", name)
    stamp = m.group(1) if m else name
    tag = m.group(2) if m else ""

    # 波形ファイルから CH と接頭辞を自動検出
    trc = sorted(scan_dir.glob("C?--*--*.trc"))
    if not trc:
        raise FileNotFoundError(f".trc ファイルがありません: {scan_dir}")
    prefix = trc[0].name.split("--")[1]
    channels = tuple(ch for ch in ALL_CHANNELS
                     if any(f.name.startswith(ch + "--") for f in trc))

    log_path = Path(log_csv).resolve() if log_csv else \
        _find_log_csv(scan_dir, stamp)

    comments: list[str] = []
    rows: list[dict] = []
    if log_path is not None and log_path.exists():
        with open(log_path, newline="", encoding="utf-8") as f:
            for rec in csv.reader(f):
                if not rec:
                    continue
                if rec[0].lstrip().startswith("#"):
                    comments.append(",".join(rec))
                    continue
                if rec[0] == "index":
                    continue
                rows.append({
                    "index": int(rec[0]), "x": float(rec[1]),
                    "y": float(rec[2]), "z": float(rec[3]),
                    "tune": float(rec[4]) if rec[4].strip() else np.nan,
                    "ok": rec[5].strip() == "ok",
                })
    else:
        # ログが見つからない場合: ファイルの連番から最低限の情報で進める
        idxs = sorted({int(f.stem.split("--")[-1]) for f in trc})
        rows = [{"index": i, "x": np.nan, "y": np.nan, "z": np.nan,
                 "tune": np.nan, "ok": True} for i in idxs]

    header = _parse_header_comments(comments)
    mode = header.get("mode", _mode_from_tag(tag))
    prefix = header.get("prefix", prefix)

    info = ScanInfo(scan_dir=scan_dir, name=name, stamp=stamp, tag=tag,
                    mode=mode, prefix=prefix, channels=channels,
                    log_csv=log_path, header=header)
    info.index = np.array([r["index"] for r in rows], int)
    info.x_um = np.array([r["x"] for r in rows])
    info.y_um = np.array([r["y"] for r in rows])
    info.z_um = np.array([r["z"] for r in rows])
    info.tune = np.array([r["tune"] for r in rows])
    info.ok = np.array([r["ok"] for r in rows], bool)

    # 公称座標: スキャン時の回転追従 (Y)・フォーカス追従 (Z) 補正を外す
    x0 = info.x_um[0] if len(info.x_um) and np.isfinite(info.x_um[0]) else 0.0
    base = header.get("base_pos_um", [x0, 0.0, 0.0])
    dydx = header.get("dydx", 0.0) if header.get("rot_correct") else 0.0
    dzdx = header.get("dzdx", 0.0) if header.get("tilt_correct") else 0.0
    dzdy = header.get("dzdy", 0.0) if header.get("tilt_correct") else 0.0
    info.xn = info.x_um.copy()
    info.yn = info.y_um - dydx * (info.x_um - x0)
    info.zn = info.z_um - dzdx * (info.x_um - base[0]) \
        - dzdy * (info.y_um - base[1])
    return info


def resolve_scan_dir(arg: str) -> Path:
    """引数をスキャンフォルダに解決する (パス or スキャン名で data\\ 内検索)。"""
    p = Path(arg)
    if p.is_dir():
        return p.resolve()
    ws = Path(__file__).resolve().parents[2]
    hits = sorted((ws / "data").glob(f"*/*/{arg}"))
    if len(hits) == 1:
        return hits[0].resolve()
    if len(hits) > 1:
        raise FileNotFoundError(
            f"同名のスキャンが複数あります: {[str(h) for h in hits]}")
    raise FileNotFoundError(f"スキャンフォルダが見つかりません: {arg}")
