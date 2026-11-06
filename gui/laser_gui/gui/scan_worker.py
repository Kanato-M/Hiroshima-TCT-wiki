# -*- coding: utf-8 -*-
"""Phase 3: 自動スキャンシーケンスの実行スレッド。

スキャンを不具合なく完了させるため、開始時は必ず次の順序で準備する
(2026-07 実機デバッグで確定した流れ):
  1. オシロスコープの設定変更 — アベレージングが ON なら OFF にする
     (リアルタイム波形でないとシーケンス/SINGLE の完了判定が機能しない)
     → シーケンス設定 → 波形保存設定 (内蔵連番カウンタは使わない、下記)
  2. レーザー設定 (要求時の自動 ON)
  3. スキャン開始

各測定点の処理:
  1. 指定座標へステージ移動 → 移動完了待ち
     (★絶対位置移動 (A:W) ではなく相対位置移動 (M:W) を使う。
      論理原点はあらかじめ C1 エッジ等で R:W リセットして決めておく)
  2. 整定時間の待機 → 画面ミラー一時停止 → オシロのスイープ積算リセット
  3. SINGLE 取得を武装し、シーケンス完了 (STOP) を待機
  4. 波形をオシロ本体ディスクへ保存 → 画面ミラー再開
  5. 保存検証: オシロ側フォルダのファイル数が増えたか確認
  6. 次の点へ移動して繰り返し

ファイル名の連番 (2026-09-07 修正):
  オシロ内蔵の連番サフィックス (EnableCounterSuffix) はカウンタが
  増えず同名上書きになる事象が実機で発生した (2 スキャン全点が
  CX--scan--00000.trc に上書きされ消失)。このため連番は PC 側で管理し、
  点ごとに TraceTitle="scan--00042" を明示して保存する
  (ファイル名は従来と同じ C2--scan--00042.trc 形式)。さらに点ごとに
  保存先のファイル数を確認し、2 点連続で増えていなければスキャンを
  中断してエラーにする (数時間分のサイレント損失防止)。

画面ミラーリング (2026-09-07 変更):
  従来はスキャン全体でミラーを止めていたが、SCDP が危険なのは
  シーケンス取得中だけなので、一時停止を「積算リセット〜保存完了」の
  取得ウィンドウに限定した。移動・整定中はミラーが再開されるため、
  スキャン中も点ごとに直前の取得波形が画面に表示される。
  (ミラーの SCDP 読み出しがロックを持っている間は次コマンドが最大
  1 フレーム分待たされるが、VICP 側の再同期 (_drain) で応答ズレは
  起きない)

スキャンモード:
  - 1d      : 1軸の位置スキャン (例: ストリップ横断 C0→C5 / 長手 9.3 mm)
  - 2d      : 2軸ラスタースキャン (蛇行オプション付き。TCT Scan プラン相当)
  - tune    : 固定位置で TUNE 値を振るエネルギースキャン (tune 50–90%)
  - 1d_tune : 1軸位置 × TUNE の複合スキャン — 各位置で TUNE を掃引する
              (位置が外側ループ、TUNE が内側ループ。ステージ移動は
              位置が変わるときだけ発生する)。位置 step は広め (50 µm
              程度) を推奨

1d/2d ではセンサー傾き補正 (フォーカス追従 Z / 回転追従 Y) を適用できる
(build_points の docstring と scan_geometry.py 参照)。

各点の座標・TUNE・所要時間・結果は PC 側の CSV に記録し、
オシロ側の連番保存ファイルとの対応付けに使う。
"""

from __future__ import annotations

import csv
import datetime
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

AXIS_NAMES = ("X", "Y", "Z")


def frange(start: float, stop: float, step: float) -> list[float]:
    """start から stop まで step 刻みの値リスト (両端含む、丸め誤差対策付き)。"""
    if step <= 0:
        raise ValueError("ステップは正の値にしてください")
    n = int(round(abs(stop - start) / step))
    sign = 1.0 if stop >= start else -1.0
    values = [start + sign * step * i for i in range(n + 1)]
    # 丸めで stop を超えた最終点を補正
    if (sign > 0 and values[-1] > stop + 1e-9) or \
       (sign < 0 and values[-1] < stop - 1e-9):
        values[-1] = stop
    return values


def _z_mode(params: dict) -> str:
    """Z 追従の方式を返す (旧パラメータ tilt_correct との互換込み)。

      "off"          : Z を動かさない
      "coef_origin"  : 較正係数の平面を論理原点 (0,0,0)=合焦 に固定する
      "coef_current" : 較正係数の平面をスキャン開始時の現在位置に固定する
      "points"       : スキャン範囲の始点・終点 (2D は四隅) の実測 Z を
                       線形 (双線形) 補間する
    """
    mode = params.get("z_mode")
    if mode is None:
        mode = "coef_current" if params.get("tilt_correct") else "off"
    if mode not in ("off", "coef_origin", "coef_current", "points"):
        raise ValueError(f"不明な Z 追従方式: {mode}")
    return mode


def _frac(value: float, start: float, stop: float) -> float:
    """start→stop を 0→1 とした位置 (幅 0 のときは 0)。"""
    return 0.0 if stop == start else (value - start) / (stop - start)


def build_points(params: dict, base_pos: list[float]) -> list[dict]:
    """スキャンパラメータから測定点リストを生成する。

    各点は {"pos": [x, y, z] (µm), "tune": float or None}。
    base_pos はスキャン対象外の軸の座標として使う (スキャン開始時の現在位置)。

    センサー傾き補正:
      - 回転追従 (rot_correct): ストリップ中心の Y は X に沿って
        ドリフトする (dY/dX) ため、X スキャン開始位置を基準に Y を
        シフトし、常にストリップと同じ相対位置を走る。
      - Z 追従 (z_mode, _z_mode 参照):
        * 係数方式: フォーカス面を平面 z = dzdx·x + dzdy·y で近似する。
          "coef_origin" は論理原点で合焦 (原点出し直後の状態) を前提に
          平面を原点へ固定するので、**スキャン開始時にステージがどこに
          あっても同じ Z になる**。"coef_current" は従来方式で、開始時の
          現在位置で合焦していることが前提。
        * 実測方式 "points": 始点・終点 (1D) または四隅 (2D) で実測した
          合焦 Z (z_points, 論理座標の絶対値) を線形 / 双線形補間する。
          係数の較正が不要で、スキャン範囲内の誤差が端点の実測精度で
          決まる。z_points の並びは
            1D: [始点, 終点]
            2D: [高速始×低速始, 高速終×低速始, 高速始×低速終, 高速終×低速終]
      Z 軸そのものをスキャンする場合は Z を上書きしない。
    """
    mode = params["mode"]
    points: list[dict] = []

    if mode in ("1d", "2d", "1d_tune"):
        fast_axis = params["fast_axis"]
        fast_vals = frange(params["fast_start"], params["fast_stop"],
                           params["fast_step"])
        if mode == "2d":
            slow_axis = params["slow_axis"]
            if slow_axis == fast_axis:
                raise ValueError("高速軸と低速軸は別の軸にしてください")
            slow_vals = frange(params["slow_start"], params["slow_stop"],
                               params["slow_step"])
        else:
            slow_axis, slow_vals = None, [None]
        # 1d_tune: 各位置での TUNE 内側ループ
        tune_vals = None
        if mode == "1d_tune":
            tune_vals = frange(params["tune_start"], params["tune_stop"],
                               params["tune_step"])

        scan_axes = {fast_axis, slow_axis} if mode == "2d" else {fast_axis}
        # X をスキャンしない場合は補正量 0 (基準=現在位置で差分が出ない)
        rot = bool(params.get("rot_correct")) and 0 in scan_axes
        # Z 自体をスキャンする場合 (フォーカス探索等) は Z を上書きしない
        z_mode = "off" if 2 in scan_axes else _z_mode(params)
        dydx = params.get("rot_dydx", 0.0)
        dzdx = params.get("tilt_dzdx", 0.0)
        dzdy = params.get("tilt_dzdy", 0.0)
        z_pts = list(params.get("z_points") or [])
        if z_mode == "points":
            need = 4 if mode == "2d" else 2
            if len(z_pts) < need:
                raise ValueError(
                    f"実測 Z が {need} 点必要です (現在 {len(z_pts)} 点)")
        if fast_axis == 0:
            x_ref = params["fast_start"]
        elif mode == "2d" and slow_axis == 0:
            x_ref = params["slow_start"]
        else:
            x_ref = base_pos[0]

        for j, sv in enumerate(slow_vals):
            # 蛇行 (往復) スキャン: 奇数行は逆順に走ることで移動時間を短縮
            row = list(fast_vals)
            if params.get("snake", True) and j % 2 == 1:
                row.reverse()
            for fv in row:
                pos = list(base_pos)
                pos[fast_axis] = fv
                if sv is not None:
                    pos[slow_axis] = sv
                if rot:
                    pos[1] += dydx * (pos[0] - x_ref)
                if z_mode == "coef_origin":
                    pos[2] = dzdx * pos[0] + dzdy * pos[1]
                elif z_mode == "coef_current":
                    pos[2] = base_pos[2] + dzdx * (pos[0] - base_pos[0]) \
                                         + dzdy * (pos[1] - base_pos[1])
                elif z_mode == "points":
                    s = _frac(fv, params["fast_start"], params["fast_stop"])
                    z = z_pts[0] + s * (z_pts[1] - z_pts[0])
                    if sv is not None:
                        t = _frac(sv, params["slow_start"],
                                  params["slow_stop"])
                        z_far = z_pts[2] + s * (z_pts[3] - z_pts[2])
                        z += t * (z_far - z)
                    pos[2] = z
                if tune_vals is None:
                    points.append({"pos": pos, "tune": None})
                else:
                    for tv in tune_vals:
                        points.append({"pos": list(pos), "tune": tv})

    elif mode == "tune":
        for tv in frange(params["tune_start"], params["tune_stop"],
                         params["tune_step"]):
            points.append({"pos": list(base_pos), "tune": tv})
    else:
        raise ValueError(f"不明なスキャンモード: {mode}")

    return points


def scan_corners(params: dict, base_pos: list[float]) -> list[list[float]]:
    """実測 Z 方式の測定位置 (始点・終点 / 四隅) の XY を返す。

    並びは build_points の z_points と同じ。回転追従のシフトを含む
    実際の移動先で、Z は base_pos のまま (その場で合焦させて実測する)。
    """
    p = dict(params, z_mode="off", snake=False)
    if p["mode"] == "1d_tune":
        p["mode"] = "1d"
    if p["mode"] not in ("1d", "2d"):
        return []
    pts = build_points(p, base_pos)
    n_fast = len(frange(p["fast_start"], p["fast_stop"], p["fast_step"]))
    idx = [0, n_fast - 1]
    if p["mode"] == "2d":
        idx += [len(pts) - n_fast, len(pts) - 1]
    return [list(pts[i]["pos"]) for i in idx]


def count_points(params: dict) -> int:
    """UI の見積もり表示用: 測定点数だけを計算する。"""
    return len(build_points(params, [0.0, 0.0, 0.0]))


class ScanWorker(QThread):
    """スキャンシーケンスを実行するスレッド。

    デバイスラッパは全てロックで直列化されているため、GUI 側の
    ポーリング (位置表示・画面ミラー等) と並行して安全に使える。
    """

    progress = Signal(int, int, str)      # (完了点数, 総点数, 状況メッセージ)
    finished_scan = Signal(bool, str)     # (正常完了か, メッセージ)

    def __init__(self, scope, stage, laser, params: dict,
                 csv_path: str | Path, parent=None):
        super().__init__(parent)
        self.scope = scope
        self.stage = stage
        self.laser = laser
        self.params = params
        self.csv_path = Path(csv_path)
        self._abort = False
        self._file_count = -1   # 保存検証: オシロ側フォルダのファイル数
        self._save_miss = 0     # 保存検証: 連続で増えなかった回数

    def abort(self):
        """スキャン中断要求 (次のチェックポイントで停止する)。"""
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        p = self.params
        laser_turned_on = False
        try:
            # --- 準備: 現在位置 (論理座標) を基準に測定点リストを作る ---
            if self.stage.connected:
                base_pos, _ = self.stage.get_position_um()
            else:
                base_pos = [0.0, 0.0, 0.0]  # tune スキャンはステージ不要
            points = build_points(p, base_pos)
            total = len(points)

            # === 手順1: オシロの設定変更 ===
            # アベレージングは必ず OFF: リアルタイム波形にしないと
            # SINGLE/シーケンス取得の完了判定 (STOP 待ち) が機能しない
            if p.get("avg_off", True):
                self.scope.set_averaging_off()
            if p.get("use_sequence"):
                self.scope.set_sequence(True, p["segments"])
            # 連番は PC 側で管理する (counter=False)。内蔵カウンタは
            # 増えないことがあり同名上書きで全点消失した (2026-09-07)
            self.scope.configure_waveform_save(
                p["save_dir"], p["prefix"],
                source=p.get("save_source", "AllDisplayed"),
                fmt=p.get("save_format", "Binary"), counter=False)
            # 保存検証の基準値 (スキャン開始時点のファイル数。通常 0)
            self._file_count = self.scope.count_saved_files(p["save_dir"])
            self._save_miss = 0

            # === 手順2: レーザー設定 (要求時の自動 ON) ===
            if p.get("laser_on") and self.laser.connected:
                self.laser.set_emission(True)
                laser_turned_on = True

            # === 手順3: スキャン開始 ===
            # 画面ミラーは止めない: SCDP が危険な取得ウィンドウ中だけ
            # _measure_point 内で一時停止し、移動・整定中は動かす
            self._write_csv_header(p, total, base_pos)

            t_scan_start = time.monotonic()
            done = 0
            for i, pt in enumerate(points):
                if self._abort:
                    break
                # 経過から平均時間を出して残り時間 (ETA) を見積もる
                if done > 0:
                    eta = (time.monotonic() - t_scan_start) / done * (total - done)
                    eta_str = f" / 残り約 {eta / 60:.1f} 分"
                else:
                    eta_str = ""
                x, y, z = pt["pos"]
                desc = f"X={x:.1f} Y={y:.1f} Z={z:.1f} µm"
                if pt["tune"] is not None:
                    desc += f" TUNE={pt['tune']:.1f}%"
                self.progress.emit(done, total,
                                   f"点 {i + 1}/{total}: {desc}{eta_str}")

                status = self._measure_point(pt, i)
                done += 1
                self._write_csv_row(i, pt, status)
                if status in ("ok", "timeout"):
                    self._verify_saved(i)

            if self._abort:
                self._safe_stop()
                self.finished_scan.emit(
                    False, f"スキャンを中断しました ({done}/{total} 点完了)")
            else:
                elapsed = (time.monotonic() - t_scan_start) / 60
                self.progress.emit(done, total, "完了")
                self.finished_scan.emit(
                    True,
                    f"スキャン完了: {done} 点 / {elapsed:.1f} 分\n"
                    f"記録: {self.csv_path}")
        except Exception as exc:
            self._safe_stop()
            self.finished_scan.emit(False, f"スキャンエラー: {exc}")
        finally:
            # 安全網: 取得ウィンドウ内で例外・中断した場合もミラーを再開する
            self.scope.mirror_paused = False
            # 安全のため、自動 ON したレーザー (または要求時) は必ず OFF に戻す
            if p.get("laser_off", True) and laser_turned_on \
                    and self.laser.connected:
                try:
                    self.laser.set_emission(False)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def _measure_point(self, pt: dict, index: int) -> str:
        """1測定点の処理: (TUNE設定) → 移動 → 整定 → 取得 → 保存。

        保存ファイル名の連番は index から明示指定する
        (title="scan--00042" → C2--scan--00042.trc。CSV の index と
        常に一致し、オシロ内蔵カウンタに依存しない)。
        """
        p = self.params
        title = f"{p['prefix']}--{index:05d}"

        # TUNE スキャン時はマニュアルモードで値を設定
        if pt["tune"] is not None:
            self.laser.set_tune_mode(False)
            self.laser.set_tune_percent(pt["tune"])

        # ステージ移動 → 完了待ち (tune スキャンは固定位置なので動かさない)。
        # 移動は相対位置移動 (M:W): 絶対位置移動 (A:W) では不具合があった
        # ため、論理原点 (外枠エッジ=0,0,0) 基準の座標へ差分パルスで移動する。
        # 1d_tune では同一位置の連続点 (TUNE 内側ループ) は差分 0 パルスの
        # 移動になるだけで実質動かない
        if p["mode"] in ("1d", "2d", "1d_tune"):
            x, y, z = pt["pos"]
            self.stage.move_to_um_relative(x, y, z)
            self.stage.wait_ready(timeout=p.get("move_timeout_s", 120),
                                  abort_check=lambda: self._abort)
            if self._abort:
                return "aborted"

        # 整定待ち (振動が収まるまで)。この間は画面ミラーが動いていて、
        # 直前の点の取得波形がリアルタイムに確認できる
        self._sleep_abortable(p["settle_s"])
        if self._abort:
            return "aborted"

        # 取得ウィンドウ: この間だけ画面ミラーを止める (シーケンス取得中の
        # SCDP はタイムアウト・応答ズレ・取得への干渉の原因になるため)
        self.scope.mirror_paused = True
        try:
            # 取得: 積算リセット → SINGLE 武装 → 完了 (STOP) 待ち
            self.scope.clear_sweeps()
            self.scope.arm_single()
            deadline = time.monotonic() + p["point_timeout_s"]
            while time.monotonic() < deadline:
                if self._abort:
                    return "aborted"
                if self.scope.acquisition_stopped():
                    break
                time.sleep(0.2)
            else:
                # タイムアウト: 取得を止めて次の点へ (トリガー欠落等)
                self.scope.set_trigger_mode("STOP")
                self.scope.save_waveform_now(title)
                return "timeout"

            # 波形保存 (オシロ本体ディスクへ、PC 管理の連番で保存)
            self.scope.save_waveform_now(title)
            return "ok"
        finally:
            self.scope.mirror_paused = False

    def _verify_saved(self, index: int) -> None:
        """保存後にオシロ側フォルダのファイル数が増えたか確認する。

        増えていない状態が 2 点続いたらスキャンを中断する (同名上書き等で
        データが残らないまま数時間走るのを防ぐ)。ファイル数を確認できない
        環境 (VBS 不調など) では検証をスキップする。
        """
        n = self.scope.count_saved_files(self.params["save_dir"])
        if n < 0 or self._file_count < 0:
            return  # 確認不能: 検証なしで続行 (従来動作)
        if n > self._file_count:
            self._file_count = n
            self._save_miss = 0
            return
        self._save_miss += 1
        if self._save_miss >= 2:
            raise RuntimeError(
                f"オシロ側の保存ファイルが増えていません "
                f"(点 {index} 時点で {n} 個)。同名上書き・保存設定の"
                f"不具合の可能性があるためスキャンを中断しました。"
                f"オシロの Save Waveform 設定と保存先フォルダ "
                f"({self.params['save_dir']}) を確認してください")

    def _sleep_abortable(self, seconds: float):
        # 待ち時間・タイムアウトは単調時計で測る: 壁時計 (time.time) は
        # OS の時刻同期で不連続に飛ぶことがあり、偽のタイムアウトや
        # 待ちすぎの原因になる
        deadline = time.monotonic() + seconds
        while not self._abort:
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            time.sleep(min(0.1, remain))

    def _safe_stop(self):
        """中断・エラー時にステージとオシロを安全に止める。"""
        try:
            self.stage.stop()
        except Exception:
            pass
        try:
            self.scope.set_trigger_mode("STOP")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CSV 記録 (オシロ側の連番ファイルとの対応付け用)
    # ------------------------------------------------------------------
    def _write_csv_header(self, p: dict, total: int,
                          base_pos: list[float] | None = None):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# AC-LGAD 自動スキャン記録 "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# mode={p['mode']} total_points={total} "
                        f"segments={p.get('segments')} "
                        f"settle_s={p['settle_s']} prefix={p['prefix']} "
                        f"save_dir={p['save_dir']}"])
            # 傾き補正の係数と基準位置 (座標は補正適用後の論理座標で記録)
            base = "" if base_pos is None else \
                ",".join(f"{v:.1f}" for v in base_pos)
            z_pts = ";".join(f"{v:.1f}" for v in p.get("z_points") or [])
            w.writerow([f"# sensor={p.get('sensor_id', '')} "
                        f"sensor_type={p.get('sensor_type', 'strip')} "
                        f"z_follow={_z_mode(p)} z_points_um=[{z_pts}]"])
            w.writerow([f"# tilt_correct={_z_mode(p) != 'off'} "
                        f"rot_correct={bool(p.get('rot_correct'))} "
                        f"dzdx={p.get('tilt_dzdx', 0):.6g} "
                        f"dzdy={p.get('tilt_dzdy', 0):.6g} "
                        f"dydx={p.get('rot_dydx', 0):.6g} "
                        f"base_pos_um=({base})"])
            w.writerow(["index", "x_um", "y_um", "z_um", "tune_pct",
                        "status", "timestamp"])

    def _write_csv_row(self, index: int, pt: dict, status: str):
        x, y, z = pt["pos"]
        tune = "" if pt["tune"] is None else f"{pt['tune']:.1f}"
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                [index, f"{x:.2f}", f"{y:.2f}", f"{z:.2f}", tune,
                 status, datetime.datetime.now().isoformat()])
