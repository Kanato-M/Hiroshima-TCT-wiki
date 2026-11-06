# -*- coding: utf-8 -*-
"""Data retrieval from the oscilloscope's local disk (SSH/SFTP,
equivalent to scp).

Changes from the old SMB method (scope_files.py) (2026-09-07):
  - Connects by SSH to the OpenSSH server on the scope and fetches the
    files over SFTP (the same protocol as
    `scp -r <SCOPE_USER>@<SCOPE_IP>:C:\\...\\20260907 .` in a terminal;
    in recent OpenSSH the scp command itself uses SFTP internally).
    Much faster than SMB + SHA-256 re-read verification.
  - The password is not stored; it is entered in the GUI for every
    transfer (on the scan_panel side).
  - The retrieval unit is the "date folder": the whole of
    <scope_base>\\<year>\\<YYYYMMDD> is fetched to
    <dest_base>\\<year>\\<YYYYMMDD> on the PC.
  - Already-retrieved files (same name and size on the PC) are skipped.
    A scan folder in which every file was skipped is counted as
    "already retrieved" (duplicate-folder skip). A same-named file of a
    different size is not overwritten; it is a "conflict".
  - Verification is by size comparison (SFTP/TCP-level integrity + a
    check of the actual size after the transfer).
  - Scope-side deletion (optional) happens only when there is not a
    single error, conflict or abort: the size-verified files are deleted
    and the emptied sub-folders are cleaned up.

Scope-side prerequisite: the Windows OpenSSH server is enabled (on the
real hardware, scp connections were confirmed to work as of 2026-09).
SFTP remote paths use the "C:/Users/..." form (backslashes are converted
automatically).
"""

from __future__ import annotations

import csv
import datetime
import os
import stat as _stat
import time
from pathlib import Path
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]


class TransferAborted(Exception):
    """User abort."""


def host_from_resource(resource: str) -> str:
    """Extract the host from a VISA resource name
    (VICP::<SCOPE_IP>::INSTR etc.)."""
    parts = resource.split("::")
    return parts[1] if len(parts) > 1 else resource


def _to_sftp_path(win_path: str) -> str:
    """Windows path → SFTP path (C:\\a\\b → C:/a/b)."""
    return win_path.replace("\\", "/").rstrip("/")


class ScpFetcher:
    """Retrieve the scope's date folder to the PC over SSH/SFTP.

    Usage (from the GUI, via a worker thread):
        f = ScpFetcher(host, user, password, scope_base, dest_base)
        summary = f.fetch_date("20260907", delete_after=False, ...)
    """

    def __init__(self, host: str, username: str, password: str,
                 scope_base: str, dest_base: str | Path,
                 port: int = 22, timeout_s: float = 10.0,
                 log: LogFunc = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.scope_base = scope_base.rstrip("\\/")
        self.dest_base = Path(dest_base)
        self.timeout_s = timeout_s
        self._log = log or (lambda d, t: None)
        self._transport = None
        self._sftp = None

    # ------------------------------------------------------------------
    def connect(self) -> None:
        import paramiko
        try:
            t = paramiko.Transport((self.host, self.port))
            # better throughput for in-LAN transfers (default 2 MB →
            # 4 MB window)
            t.default_window_size = 4 * 1024 * 1024
            t.banner_timeout = self.timeout_s
            t.connect(username=self.username, password=self.password)
            self._transport = t
            self._sftp = paramiko.SFTPClient.from_transport(t)
        except paramiko.AuthenticationException:
            self.close()
            raise RuntimeError(
                f"SSH authentication failed ({self.username}@{self.host}). "
                "Check the password") from None
        except OSError as exc:
            self.close()
            raise RuntimeError(
                f"Cannot connect by SSH to the scope "
                f"{self.host}:{self.port}: "
                f"{exc}\nCheck: (1) the scope is up and answers ping "
                "(2) the OpenSSH server (sshd) is running on the scope")
        self._log("INFO", f"SSH connected: {self.username}@{self.host}")

    def close(self) -> None:
        for obj in (self._sftp, self._transport):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._sftp = None
        self._transport = None

    # ------------------------------------------------------------------
    def _walk_remote(self, top: str) -> list[tuple[str, int]]:
        """List every file (relative path, size) below the remote top."""
        files: list[tuple[str, int]] = []
        stack = [""]
        while stack:
            rel = stack.pop()
            rdir = f"{top}/{rel}" if rel else top
            for attr in self._sftp.listdir_attr(rdir):
                r = f"{rel}/{attr.filename}" if rel else attr.filename
                if _stat.S_ISDIR(attr.st_mode):
                    stack.append(r)
                else:
                    files.append((r, attr.st_size))
        files.sort()
        return files

    # ------------------------------------------------------------------
    def fetch_date(self, date_str: str, delete_after: bool = False,
                   progress=None, abort=None,
                   log_csv: str | Path | None = None) -> dict:
        """Retrieve the whole of <scope_base>/<year>/<date_str>.

        progress: callable(done, total, message)
        abort:    callable() -> bool (True aborts)
        Returns a summary dict (total/copied/already/conflicts/errors/deleted/
                bytes/seconds/aborted/can_delete/folders_skipped/records)
        """
        t0 = time.monotonic()
        year = date_str[:4]
        remote_top = _to_sftp_path(
            f"{self.scope_base}\\{year}\\{date_str}")
        dest_top = self.dest_base / year / date_str
        records: list[dict] = []
        aborted = False
        copied_bytes = 0
        can_delete = False

        self.connect()
        try:
            try:
                self._sftp.stat(remote_top)
            except FileNotFoundError:
                raise RuntimeError(
                    f"Source not found on the scope: {remote_top}")

            if progress:
                progress(0, 0, "Listing files...")
            files = self._walk_remote(remote_top)
            total = len(files)
            total_bytes = sum(s for _, s in files)

            for i, (rel, size) in enumerate(files):
                if abort is not None and abort():
                    aborted = True
                    break
                rec = {"rel": rel, "size": size, "status": "",
                       "deleted": False, "note": ""}
                src = f"{remote_top}/{rel}"
                dst = dest_top / rel.replace("/", os.sep)
                if progress:
                    progress(i, total,
                             f"Retrieve {i + 1}/{total}: {rel} "
                             f"({size / 1e6:.1f} MB)")
                try:
                    if dst.exists():
                        if dst.stat().st_size == size:
                            # already retrieved → skip
                            rec["status"] = "already"
                        else:
                            rec["status"] = "conflict"
                            rec["note"] = ("same-named file of a different "
                                           "size on the PC")
                    else:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        tmp = dst.with_name(dst.name + ".part")

                        def _cb(done_b, total_b):
                            if abort is not None and abort():
                                raise TransferAborted()
                        try:
                            self._sftp.get(src, str(tmp), callback=_cb)
                            if tmp.stat().st_size != size:
                                raise RuntimeError(
                                    f"size mismatch after transfer "
                                    f"({tmp.stat().st_size} != {size})")
                        except BaseException:
                            tmp.unlink(missing_ok=True)
                            raise
                        os.replace(tmp, dst)
                        rec["status"] = "copied"
                        copied_bytes += size
                except TransferAborted:
                    rec["status"] = "aborted"
                    records.append(rec)
                    aborted = True
                    break
                except Exception as exc:
                    rec["status"] = "error"
                    rec["note"] = str(exc)
                    self._log("ERR", f"retrieval failed {rel}: {exc}")
                records.append(rec)

            # --- delete phase: only when every file verified
            #     (copied/already) ---
            can_delete = (not aborted and len(records) == total and total > 0
                          and all(r["status"] in ("copied", "already")
                                  for r in records))
            if delete_after and can_delete:
                for r in records:
                    if abort is not None and abort():
                        aborted = True
                        break
                    dst = dest_top / r["rel"].replace("/", os.sep)
                    if not (dst.is_file()
                            and dst.stat().st_size == r["size"]):
                        r["note"] += (" delete canceled: final check on "
                                      "the PC side failed")
                        continue
                    try:
                        self._sftp.remove(f"{remote_top}/{r['rel']}")
                        r["deleted"] = True
                    except Exception as exc:
                        r["note"] += f" delete failed: {exc}"
                # clean up the emptied folders, date folder included
                # (deepest first)
                dirs = sorted({r["rel"].rsplit("/", 1)[0]
                               for r in records if "/" in r["rel"]},
                              key=len, reverse=True)
                for d in dirs + [""]:
                    try:
                        self._sftp.rmdir(f"{remote_top}/{d}" if d
                                         else remote_top)
                    except Exception:
                        pass  # keep it if not empty
        finally:
            self.close()
            if log_csv is not None:
                self._write_log_csv(log_csv, remote_top, dest_top, records)

        def count(s):
            return sum(1 for r in records if r["status"] == s)

        # "Duplicate-folder skip" tally: the number of sub-folders (scan
        # folders) in which every file had already been retrieved
        by_folder: dict[str, list[str]] = {}
        for r in records:
            folder = r["rel"].rsplit("/", 1)[0] if "/" in r["rel"] else ""
            by_folder.setdefault(folder, []).append(r["status"])
        folders_skipped = sum(
            1 for f, sts in by_folder.items()
            if f and all(s == "already" for s in sts))

        return {
            "total": total,
            "copied": count("copied"),
            "already": count("already"),
            "conflicts": count("conflict"),
            "errors": count("error"),
            "deleted": sum(1 for r in records if r["deleted"]),
            "aborted": aborted,
            "can_delete": can_delete,
            "bytes": copied_bytes,
            "seconds": time.monotonic() - t0,
            "folders_skipped": folders_skipped,
            "remote_top": remote_top,
            "dest_top": str(dest_top),
            "records": records,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _write_log_csv(path: str | Path, remote_top: str, dest_top: Path,
                       records: list[dict]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# scope→PC data retrieval log (SSH/SFTP) "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# src={remote_top} dest={dest_top}"])
            w.writerow(["rel_path", "size_bytes", "status", "deleted",
                        "note"])
            for r in records:
                w.writerow([r["rel"], r["size"], r["status"],
                            "yes" if r["deleted"] else "no", r["note"]])
