# -*- coding: utf-8 -*-
"""Z オートフォーカス (波形振幅ベース) の実行スレッド。

レーザー ON で Z を掃引し、オシロの振幅測定 (PAVA?) の極値の Z へ
自動移動する。カメラ焦点とのオフセット問題が生じない。

★前提の訂正 (2026-09-07 実測で確定)★
  フォーカスが合うほど振幅は「小さく」なる — 局所キャリア密度増による
  LGAD ゲイン抑制 (プラズマ効果)。カメラでフォーカス最良のとき振幅は
  最小だった。既定は「**信号が明瞭に見えるストリップ横のシリコン上**に
  ビームを置いて振幅最小の Z を探す」方式 (metric="min", 浅い谷の
  中心 = 焦点)。電極 (幅 ~50 µm) の直上はスポットがデフォーカスしても
  金属内に収まったままで谷が見えないため不可。旧方式の「振幅最大」
  (metric="max") も互換のため残すが、焦点には合わない可能性が高い。

自動スキャンの開始前に開始位置で実行する使い方を想定
(傾き補正 (フォーカス追従) は開始時の Z を基準面として引き継ぐ)。
通常は自動アライメントの「原点出し」がこの処理を内包している。

手順:
  1. シーケンス OFF + トリガー NORM (振幅をリアルタイムに読むため)。
     画面ミラーも一時停止する (SCDP の応答遅延防止)
  2. バックラッシュ対策: 掃引開始点よりさらに下 (-backlash) へ一度
     移動してから、下 → 上の一方向で掃引する
  3. 各 Z で整定後、PAVA を複数回読んで平均 → (Z, 振幅) カーブを記録
  4. 振幅が極値 (min: 最小 / max: 最大) の Z へ (同じく下から) 移動。
     カーブは CSV に保存
  5. 中断・エラー時は元の Z へ戻す

注意: 極値が掃引範囲の端に出た場合は範囲不足の可能性があるため、
メッセージで再実行を促す。
"""

from __future__ import annotations

import csv
import datetime
import math
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .scan_worker import frange


class AutofocusWorker(QThread):
    progress = Signal(int, int, str)      # (完了点数, 総点数, メッセージ)
    finished_focus = Signal(bool, str)    # (成功か, メッセージ)

    def __init__(self, scope, stage, params: dict, csv_path: str | Path,
                 parent=None):
        """params: ch, range_um, step_um, settle_s, reads, backlash_um,
        pava_param, move_timeout_s,
        metric: "min" (電極上で振幅最小=焦点, 既定) / "max" (旧方式)"""
        super().__init__(parent)
        self.scope = scope
        self.stage = stage
        self.params = params
        self.csv_path = Path(csv_path)
        self._abort = False

    def abort(self):
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        self.scope.mirror_paused = True
        try:
            self._run()
        except Exception as exc:
            self.finished_focus.emit(False, f"オートフォーカスエラー: {exc}")
        finally:
            self.scope.mirror_paused = False

    def _run(self):
        p = self.params
        ch = p["ch"]
        pava = p.get("pava_param", "PKPK")
        settle = p.get("settle_s", 0.3)
        reads = p.get("reads", 3)
        backlash = p.get("backlash_um", 10.0)
        timeout = p.get("move_timeout_s", 30.0)

        # 1. 振幅をリアルタイムに読める取得状態にする
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        pos, _ = self.stage.get_position_um()
        x0, y0, z0 = pos
        zs = [z0 + dz for dz in frange(-p["range_um"], p["range_um"],
                                       p["step_um"])]
        total = len(zs)

        def goto_z(z):
            self.stage.move_to_um_relative(x0, y0, z)
            self.stage.wait_ready(timeout=timeout,
                                  abort_check=lambda: self._abort)

        def restore():
            try:
                goto_z(z0)
            except Exception:
                pass

        # 2. バックラッシュ対策: 下から一方向で入る
        goto_z(zs[0] - backlash)
        if self._abort:
            restore()
            self.finished_focus.emit(False, "オートフォーカスを中断しました")
            return

        # 3. 掃引して振幅カーブを取る
        curve: list[tuple[float, float]] = []
        for i, z in enumerate(zs):
            if self._abort:
                restore()
                self.finished_focus.emit(False,
                                         "オートフォーカスを中断しました")
                return
            self.progress.emit(i, total,
                               f"オートフォーカス {i + 1}/{total}: "
                               f"Z={z:.1f} µm")
            goto_z(z)
            time.sleep(settle)
            vals = []
            for _ in range(reads):
                v, state = self.scope.measure_pava(ch, pava)
                if not math.isnan(v) and state.upper() not in ("NP", "IV"):
                    vals.append(v)
                time.sleep(0.05)
            if not vals:
                restore()
                self.finished_focus.emit(
                    False,
                    f"{ch} の振幅 ({pava}) が読めません (波形なし?)。"
                    "レーザー ON・トリガー動作中 (NORM で波形更新)・"
                    "ストリップ直上であることを確認してください")
                return
            curve.append((z, sum(vals) / len(vals)))

        # 4. 極値の Z へ移動 (同方向=下から接近して再現性を確保)
        # min: V 字の底は焦点深度分だけ平坦で左右非対称にもなるので、
        # 谷全体をローレンツ型 + 一次基線でフィットした曲線の最小点を
        # 焦点とする (単純な最小点はノイズで端に飛び得る)
        metric = p.get("metric", "min")
        edge = False
        if metric == "min":
            from .alignment_worker import dip_center, fit_focus
            zs_c = [z for z, _ in curve]
            vs_c = [v for _, v in curve]
            try:
                # ローレンツ型 + 一次基線フィットの曲線最小点 (自動
                # アライメントの精密モードと同じ推定。2026-10-05 実機
                # データで同一箇所の 2 回測定が 0.1 µm 以内に一致)
                try:
                    best_z = fit_focus(zs_c, vs_c, min_depth=0.004)["center"]
                except ValueError as exc:
                    if "端にかかって" in str(exc):
                        raise
                    best_z, _, _, _ = dip_center(
                        zs_c, vs_c, min_width=2.0,
                        max_width=4.0 * p["range_um"],
                        max_ratio=0.9, min_depth=0.004)
                best_z = round(best_z / 2.0) * 2.0
                best_v = min(curve, key=lambda t: abs(t[0] - best_z))[1]
            except ValueError:
                # 谷を特定できない (範囲不足/信号不明瞭) → 最小点+警告
                best_z, best_v = min(curve, key=lambda t: t[1])
                edge = True
        else:
            best_z, best_v = max(curve, key=lambda t: t[1])
            edge = best_z in (curve[0][0], curve[-1][0])
        goto_z(zs[0] - backlash)
        goto_z(best_z)
        self._write_csv(ch, pava, z0, best_z, curve)

        label = "最小 (谷中心=焦点)" if metric == "min" else "最大 (旧方式)"
        msg = (f"最適フォーカス: Z = {best_z:.1f} µm "
               f"(移動量 {best_z - z0:+.1f} µm)\n"
               f"{ch} {pava} {label} {best_v * 1e3:.1f} mV\n"
               f"カーブ記録: {self.csv_path}")
        if edge:
            msg += ("\n★ 谷/極値を範囲内で特定できませんでした。範囲を"
                    "広げる・電極直上に置き直すなどして再実行を推奨 ★")
        self.progress.emit(total, total, "完了")
        self.finished_focus.emit(True, msg)

    # ------------------------------------------------------------------
    def _write_csv(self, ch, pava, z0, best_z, curve):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# Z オートフォーカス {ch} {pava} "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# z_start={z0:.1f} z_best={best_z:.1f}"])
            w.writerow(["z_um", "amplitude_v"])
            for z, v in curve:
                w.writerow([f"{z:.2f}", f"{v:.6g}"])
