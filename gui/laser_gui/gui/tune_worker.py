# -*- coding: utf-8 -*-
"""TUNE 最適化 (波形振幅ベース) の実行スレッド。

PiLAS の TUNE (発振の効き) の最適値は繰り返し周波数に依存する。
40 MHz で合わせた手動 TUNE 値のまま周波数を変えると発振閾値を
下回って光が出なくなり「信号が全く見えない」状態になる (TRIG OUT は
電気的に出続けるためトリガーは掛かるが波形は平坦のまま。2026-07 実機で
確認された症状)。

このワーカーは現在の周波数のまま TUNE を高い側 (光が弱い側) から
下げながらオシロの振幅 (PAVA) を読み、目標振幅に最も近い TUNE を探して
設定する。周波数を変えたら位置・フォーカスはそのままこれを実行すれば、
その周波数での動作点が数十秒で見つかる。

測定方式 (params["channels"] の本数で決まる):
  - 単一 CH: その CH の振幅を目標に合わせる (従来方式)
  - 隣接 2ch 合計 (2026-10-07 追加): 隣り合う 2 本のストリップを読む
    CH の振幅の合計 A_lead + A_sub を目標に合わせる。AC-LGAD では
    ビーム位置を 2 ストリップ間で動かしても A_lead + A_sub の変化が
    小さい (電荷共有で分配されるだけ) ので、ストリップ中間 (ピッチ 500 µm
    なら電極から 250 µm) で合計を測れば位置ずれに鈍感に MIP 相当の
    信号量へ合わせられる。DESY ビームテスト (2026) の結果では
    ストリップ中間で A_lead + A_sub = 64 ± 1 mV (参考: ストリップ近傍の
    単一 CH で 77 mV)。params["move_to"] があれば測定前にその論理座標
    (ストリップ中間) へ移動し、終了後 params["return_to"] へ戻る。

手順:
  1. シーケンス OFF + トリガー NORM + ミラー一時停止 (振幅読み出し用)
     (必要なら測定位置へ移動)
  2. TUNE を manual モードにし、粗掃引: start (既定 100%) → stop (既定 0%)
     へ step (既定 2%) で下げながら各点で振幅を測定し、目標を跨いだら停止
  3. 精密掃引: 目標を跨いだ区間 (前の点 〜 跨いだ点) を fine_step
     (既定 0.5%) 刻み・多数回読み (中央値) で測り直し、許容 (±tol) に
     入った TUNE で停止。入らなければ測定点のうち目標に最も近い TUNE を採用
     (粗掃引だけだと ±数 mV の読み取りノイズで閾値交差が決まってしまう。
     2026-10-05 の記録では隣接 TUNE 間で 65 → 77 mV と飛んでいた)
  4. 採用 TUNE に設定して検証測定 (多数回読み) し、結果を報告
  5. 粗掃引で最後まで達しなければ振幅最大だった TUNE に設定して警告付きで
     報告 (全点でほぼ 0 のままなら元の TUNE に戻してエラー報告)。
     最初の点 (TUNE 最大 = 最弱) で既に目標を超えていれば、その TUNE に
     設定した上で減光が必要と警告する
  6. カーブ (phase, tune, 各 CH 振幅, 合計, ばらつき) は CSV に記録

前提: レーザー ON・ビームがストリップ直上または中間・フォーカス済み・
オシロのトリガーが動作していること (Z オートフォーカスと同じ)。
"""

from __future__ import annotations

import csv
import datetime
import math
import statistics
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .scan_worker import frange


class TuneOptimizeWorker(QThread):
    progress = Signal(int, int, str)    # (完了点数, 総点数, メッセージ)
    finished_tune = Signal(bool, str)   # (成功か, メッセージ)

    def __init__(self, scope, laser, params: dict, csv_path: str | Path,
                 stage=None, parent=None):
        """params:
          channels    : 測定 CH のリスト (1 本 = 単一 CH, 2 本 = 隣接 2ch 合計)
          ch          : (旧互換) channels を省略したときの単一 CH
          target_mv   : 目標振幅 (合計) [mV]
          tol_mv      : 許容誤差 [mV] (既定 1.0)
          tune_start / tune_stop / tune_step : 粗掃引 [%]
          fine_step   : 精密掃引の刻み [%] (既定 0.5, 0 なら精密掃引なし)
          settle_s    : TUNE 変更後の整定待ち [s]
          reads       : 粗掃引の読み取り回数 (既定 3)
          fine_reads  : 精密掃引・検証の読み取り回数 (既定 8)
          pava_param  : PAVA パラメータ (既定 PKPK)
          move_to     : 測定前に移動する論理座標 [x, y, z] (None なら移動なし)
          return_to   : 終了後に戻る論理座標 (None なら戻らない)
          move_timeout_s : ステージ移動のタイムアウト [s]
        """
        super().__init__(parent)
        self.scope = scope
        self.laser = laser
        self.stage = stage
        self.params = params
        self.csv_path = Path(csv_path)
        self._abort = False
        self._rows: list[dict] = []     # CSV 行 (phase, tune, amps, sum, std)
        self._channels: list[str] = list(params.get("channels")
                                         or [params["ch"]])

    def abort(self):
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        self.scope.mirror_paused = True
        try:
            self._run()
        except Exception as exc:
            self.finished_tune.emit(False, f"TUNE 最適化エラー: {exc}")
        finally:
            self.scope.mirror_paused = False

    # ------------------------------------------------------------------
    # 測定
    # ------------------------------------------------------------------
    def _read_once(self, ch: str, pava: str) -> float | None:
        v, state = self.scope.measure_pava(ch, pava)
        if math.isnan(v) or state.upper() in ("NP", "IV"):
            return None
        return v

    def _measure(self, reads: int, pava: str) -> tuple[dict, float, float]:
        """各 CH を reads 回読み、(CH→中央値 [V], 合計 [V], 合計の標準偏差)
        を返す。読めなかった回は除外し、全滅なら 0 とする。"""
        per_ch: dict[str, list[float]] = {ch: [] for ch in self._channels}
        sums: list[float] = []
        for _ in range(reads):
            vals = []
            for ch in self._channels:
                v = self._read_once(ch, pava)
                if v is not None:
                    per_ch[ch].append(v)
                vals.append(0.0 if v is None else v)
            sums.append(sum(vals))
            time.sleep(0.05)
        amps = {ch: (statistics.median(v) if v else 0.0)
                for ch, v in per_ch.items()}
        total = sum(amps.values())
        std = statistics.pstdev(sums) if len(sums) > 1 else 0.0
        return amps, total, std

    def _point(self, phase: str, tv: float, reads: int, settle: float,
               pava: str) -> float:
        """TUNE を tv に設定して測定し、記録して合計 [V] を返す。"""
        self.laser.set_tune_percent(tv)
        time.sleep(settle)
        amps, total, std = self._measure(reads, pava)
        self._rows.append({"phase": phase, "tune": tv, "amps": amps,
                           "sum": total, "std": std})
        return total

    def _fmt(self, row: dict) -> str:
        amps = row["amps"]
        if len(self._channels) == 1:
            return f"{self._channels[0]} {row['sum'] * 1e3:.1f} mV"
        parts = " + ".join(f"{ch} {amps[ch] * 1e3:.1f}"
                           for ch in self._channels)
        return f"{parts} = {row['sum'] * 1e3:.1f} mV"

    # ------------------------------------------------------------------
    # ステージ移動 (ストリップ中間測定用)
    # ------------------------------------------------------------------
    def _goto(self, pos, timeout: float) -> None:
        x, y, z = pos
        self.stage.move_to_um_relative(x, y, z)
        self.stage.wait_ready(timeout=timeout,
                              abort_check=lambda: self._abort)

    def _return(self) -> str:
        """終了時に元の位置へ戻る。戻れなかった場合は注意文を返す。"""
        pos = self.params.get("return_to")
        if pos is None or self.stage is None:
            return ""
        try:
            self._abort = False      # 中断後でも復帰移動は完了させる
            self._goto(pos, self.params.get("move_timeout_s", 30.0))
            return ""
        except Exception as exc:
            return (f"\n注意: 元の位置 ({pos[0]:.0f}, {pos[1]:.0f}, "
                    f"{pos[2]:.0f}) への復帰に失敗しました: {exc}")

    # ------------------------------------------------------------------
    def _run(self):
        p = self.params
        pava = p.get("pava_param", "PKPK")
        settle = p.get("settle_s", 0.3)
        reads = p.get("reads", 3)
        fine_reads = p.get("fine_reads", 8)
        fine_step = p.get("fine_step", 0.5)
        tol_v = p.get("tol_mv", 1.0) / 1e3
        target_v = p["target_mv"] / 1e3
        chs = "+".join(self._channels)
        label = (f"{chs} 合計" if len(self._channels) > 1 else chs)

        # 1. 振幅をリアルタイムに読める取得状態にする
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        # 現在の状態 (周波数・TUNE) を控える
        st = self.laser.get_status()
        freq_hz = st["int_freq_hz"]
        # 中断・失敗時に戻す元の TUNE [%] (mState は 0.01% 単位。旧版は
        # 換算を誤って 10 倍の値を控えており、戻すときに 100% へ張り付いた)
        tune0 = st["tune_percent"]

        # 測定位置 (ストリップ中間) へ移動
        move_to = p.get("move_to")
        if move_to is not None:
            if self.stage is None:
                raise RuntimeError("測定位置への移動にはステージ接続が必要です")
            self.progress.emit(0, 0,
                               f"測定位置 ({move_to[0]:.0f}, {move_to[1]:.0f}, "
                               f"{move_to[2]:.0f}) µm へ移動中...")
            self._goto(move_to, p.get("move_timeout_s", 30.0))
            if self._abort:
                self.finished_tune.emit(
                    False, "TUNE 最適化を中断しました (TUNE は変更していません)"
                    + self._return())
                return
            time.sleep(0.2)

        self.laser.set_tune_mode(False)  # manual

        def finish(ok: bool, message: str):
            self._write_csv(pava, freq_hz)
            self.finished_tune.emit(ok, message + self._return())

        def aborted():
            self.laser.set_tune_percent(tune0)
            finish(False, "TUNE 最適化を中断しました "
                          f"(TUNE を {tune0:.1f}% に戻しました)")

        # 2. 粗掃引: 目標を跨ぐまで TUNE を下げる
        tunes = frange(p.get("tune_start", 100.0), p.get("tune_stop", 0.0),
                       p.get("tune_step", 2.0))
        total = len(tunes)
        coarse: list[tuple[float, float]] = []
        cross = None      # 目標に達した (index)
        for i, tv in enumerate(tunes):
            if self._abort:
                aborted()
                return
            self.progress.emit(i, total,
                               f"粗掃引 {i + 1}/{total}: TUNE {tv:.1f}%")
            amp = self._point("coarse", tv, reads, settle, pava)
            coarse.append((tv, amp))
            self.progress.emit(i + 1, total,
                               f"粗掃引 TUNE {tv:.1f}%: {self._fmt(self._rows[-1])}")
            if amp >= target_v - tol_v:
                cross = i
                break

        tgt = f"{p['target_mv']:.0f} mV"
        tol_txt = f"±{p['tol_mv']:.1f}" if "tol_mv" in p else ""

        if cross is None:
            # 目標未達: 振幅最大の TUNE を採用 (全滅なら元に戻す)
            best_tv, best_amp = max(coarse, key=lambda t: t[1])
            if best_amp * 1e3 >= 5.0:  # ノイズ (数 mV) より明確に大きい
                self.laser.set_tune_percent(best_tv)
                finish(True,
                       f"目標 {tgt} ({label}) には未達ですが、振幅最大の "
                       f"TUNE = {best_tv:.1f}% ({best_amp * 1e3:.1f} mV) に"
                       f"設定しました\n周波数 {freq_hz / 1e6:.4g} MHz では"
                       "これ以上の振幅が出ません。エネルギー要件を満たすか"
                       f"確認してください\nカーブ記録: {self.csv_path}")
            else:
                self.laser.set_tune_percent(tune0)
                finish(False,
                       f"全 TUNE で信号がほぼ 0 でした (最大 {best_amp * 1e3:.1f} "
                       f"mV @ {best_tv:.1f}%)。TUNE を {tune0:.1f}% に戻しました\n"
                       "確認: レーザー ON / ビームがストリップ上 / フォーカス / "
                       f"{chs} の結線・トリガー動作\nカーブ記録: {self.csv_path}")
            return

        # 3. 精密掃引: 跨いだ区間 (前の点 〜 跨いだ点) を細かく測り直す
        candidates: list[tuple[float, float]] = [coarse[cross]]
        lo_tv, lo_amp = coarse[cross]            # 目標以上 (TUNE 低い側)
        hi_tv = coarse[cross - 1][0] if cross > 0 else None
        if hi_tv is not None:
            candidates.append(coarse[cross - 1])
        in_tol = abs(lo_amp - target_v) <= tol_v
        if hi_tv is not None and fine_step > 0 and not in_tol \
                and hi_tv - lo_tv > fine_step * 1.5:
            fine = frange(hi_tv - fine_step, lo_tv + fine_step, fine_step)
            n = len(fine)
            for j, tv in enumerate(fine):
                if self._abort:
                    aborted()
                    return
                self.progress.emit(j, n,
                                   f"精密掃引 {j + 1}/{n}: TUNE {tv:.1f}%")
                amp = self._point("fine", tv, fine_reads, settle, pava)
                candidates.append((tv, amp))
                self.progress.emit(
                    j + 1, n, f"精密掃引 TUNE {tv:.1f}%: "
                    f"{self._fmt(self._rows[-1])}")
                if abs(amp - target_v) <= tol_v:
                    break
        elif hi_tv is not None and not in_tol:
            # 区間が狭い (粗 step が既に細かい): 前の点も多数回読みで測り直す
            if self._abort:
                aborted()
                return
            amp = self._point("fine", hi_tv, fine_reads, settle, pava)
            candidates.append((hi_tv, amp))

        # 4. 目標に最も近い TUNE を採用し、検証測定
        best_tv, _ = min(candidates, key=lambda t: abs(t[1] - target_v))
        if self._abort:
            aborted()
            return
        self.progress.emit(0, 0, f"TUNE {best_tv:.1f}% で検証測定中...")
        self._point("verify", best_tv, fine_reads, settle, pava)
        row = self._rows[-1]
        amp = row["sum"]
        dev_mv = (amp - target_v) * 1e3
        ok_tol = abs(amp - target_v) <= tol_v
        self.progress.emit(1, 1, "完了")

        first_tv, first_amp = coarse[0]
        if cross == 0 and first_amp > target_v + tol_v:
            # TUNE 最大 (最弱) でも目標超過: レーザー側では下げられない
            finish(True,
                   f"TUNE 最大 ({first_tv:.1f}%) でも目標 {tgt} を超えて"
                   f"います: {self._fmt(row)}\n"
                   f"TUNE = {best_tv:.1f}% に設定しましたが、この周波数では"
                   "これ以上弱くできません。ND フィルタ等で減光するか、"
                   "目標を見直してください\n"
                   f"周波数 {freq_hz / 1e6:.4g} MHz / 検証 ±{row['std'] * 1e3:.1f} mV"
                   f" (std, {fine_reads} 回)\nカーブ記録: {self.csv_path}")
            return

        head = (f"目標 {tgt} {tol_txt} ({label}) に到達" if ok_tol
                else f"目標 {tgt} {tol_txt} ({label}) に最も近い点を採用 "
                     f"(偏差 {dev_mv:+.1f} mV)")
        finish(True,
               f"{head}: TUNE = {best_tv:.1f}%\n"
               f"検証: {self._fmt(row)} (std ±{row['std'] * 1e3:.1f} mV, "
               f"{fine_reads} 回)\n"
               f"周波数 {freq_hz / 1e6:.4g} MHz / TUNE は manual "
               f"{best_tv:.1f}% に設定済み\nカーブ記録: {self.csv_path}")

    # ------------------------------------------------------------------
    def _write_csv(self, pava, freq_hz):
        p = self.params
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        move_to = p.get("move_to")
        pos = "" if move_to is None else "/".join(f"{v:.1f}" for v in move_to)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# TUNE 最適化 {'+'.join(self._channels)} {pava} "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# freq_hz={freq_hz} target_mv={p['target_mv']} "
                        f"tol_mv={p.get('tol_mv', '')} "
                        f"mode={'sum2' if len(self._channels) > 1 else 'single'} "
                        f"pos_um={pos}"])
            w.writerow(["phase", "tune_pct"]
                       + [f"{ch}_v" for ch in self._channels]
                       + ["sum_v", "sum_std_v"])
            for r in self._rows:
                w.writerow([r["phase"], f"{r['tune']:.1f}"]
                           + [f"{r['amps'][ch]:.6g}" for ch in self._channels]
                           + [f"{r['sum']:.6g}", f"{r['std']:.3g}"])
