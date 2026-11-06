# -*- coding: utf-8 -*-
"""オシロ → PC データ回収の実行スレッド (SSH/SFTP)。

実体は devices.scope_scp.ScpFetcher (日付フォルダ単位の SFTP 回収)。
2026-09-07 に SMB 方式 (devices/scope_files.py, 現在は未使用) から
仕様変更した。スキャンと同時に走らせない排他制御は scan_panel 側で行う。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal


class TransferWorker(QThread):
    progress = Signal(int, int, str)     # (完了数, 総数, 状況メッセージ)
    finished_transfer = Signal(bool, str)  # (成功か, メッセージ)

    def __init__(self, fetcher, date_str: str, delete_after: bool,
                 log_csv: str | Path, parent=None):
        super().__init__(parent)
        self.fetcher = fetcher
        self.date_str = date_str
        self.delete_after = delete_after
        self.log_csv = Path(log_csv)
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        try:
            s = self.fetcher.fetch_date(
                self.date_str,
                delete_after=self.delete_after,
                progress=lambda d, t, m: self.progress.emit(d, t, m),
                abort=lambda: self._abort,
                log_csv=self.log_csv)
        except Exception as exc:
            self.finished_transfer.emit(False, f"転送エラー: {exc}")
            return

        mb = s["bytes"] / 1e6
        rate = mb / s["seconds"] if s["seconds"] > 0 else 0.0
        lines = [
            f"対象 {s['total']} 件: 新規コピー {s['copied']} / "
            f"回収済みスキップ {s['already']} / 競合 {s['conflicts']} / "
            f"エラー {s['errors']}",
        ]
        if s["folders_skipped"]:
            lines.append(f"回収済みフォルダ {s['folders_skipped']} 個を"
                         "スキップしました")
        lines.append(f"転送量 {mb:.1f} MB / {s['seconds']:.0f} 秒 "
                     f"({rate:.1f} MB/s)")
        if self.delete_after:
            if s["deleted"]:
                lines.append(f"オシロ側を {s['deleted']} 件削除しました "
                             "(全件のサイズ検証成功を確認済み)")
            elif not s["can_delete"]:
                lines.append("★ 未検証のファイルがあるためオシロ側は"
                             "削除していません (再実行で解消可) ★")
        lines.append(f"回収先: {s['dest_top']}")
        lines.append(f"記録: {self.log_csv}")

        if s["aborted"]:
            self.finished_transfer.emit(
                False, "転送を中断しました\n" + "\n".join(lines))
        elif s["errors"] or s["conflicts"]:
            self.finished_transfer.emit(
                False, "一部のファイルを回収できませんでした\n"
                + "\n".join(lines))
        elif s["total"] == 0:
            self.finished_transfer.emit(True, "回収対象のファイルはありません")
        else:
            self.finished_transfer.emit(True, "転送完了\n" + "\n".join(lines))
