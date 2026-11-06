# -*- coding: utf-8 -*-
"""Execution thread of the scope → PC data retrieval (SSH/SFTP).

The actual work is done by devices.scope_scp.ScpFetcher (SFTP retrieval
per date folder). On 2026-09-07 the spec changed from the SMB method
(devices/scope_files.py, currently unused). Mutual exclusion with scans
is handled by scan_panel.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal


class TransferWorker(QThread):
    progress = Signal(int, int, str)     # (done, total, status message)
    finished_transfer = Signal(bool, str)  # (success?, message)

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
            self.finished_transfer.emit(False, f"Transfer error: {exc}")
            return

        mb = s["bytes"] / 1e6
        rate = mb / s["seconds"] if s["seconds"] > 0 else 0.0
        lines = [
            f"{s['total']} files: newly copied {s['copied']} / "
            f"already retrieved {s['already']} / conflicts {s['conflicts']} / "
            f"errors {s['errors']}",
        ]
        if s["folders_skipped"]:
            lines.append(f"Skipped {s['folders_skipped']} already-retrieved "
                         "folder(s)")
        lines.append(f"Transferred {mb:.1f} MB in {s['seconds']:.0f} s "
                     f"({rate:.1f} MB/s)")
        if self.delete_after:
            if s["deleted"]:
                lines.append(f"Deleted {s['deleted']} files on the scope "
                             "(size check passed for all files)")
            elif not s["can_delete"]:
                lines.append("★ Some files are unverified, so nothing was "
                             "deleted on the scope (a re-run can resolve "
                             "this) ★")
        lines.append(f"Destination: {s['dest_top']}")
        lines.append(f"Log: {self.log_csv}")

        if s["aborted"]:
            self.finished_transfer.emit(
                False, "Transfer aborted\n" + "\n".join(lines))
        elif s["errors"] or s["conflicts"]:
            self.finished_transfer.emit(
                False, "Some files could not be retrieved\n"
                + "\n".join(lines))
        elif s["total"] == 0:
            self.finished_transfer.emit(True, "No files to retrieve")
        else:
            self.finished_transfer.emit(
                True, "Transfer complete\n" + "\n".join(lines))
