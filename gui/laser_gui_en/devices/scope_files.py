# -*- coding: utf-8 -*-
"""Data retrieval from the oscilloscope's local disk (via SMB file
sharing).

The LeCroy WavePro is Windows-based, so connecting to its administrative
share (default C$) with the scope's Windows account (default <SCOPE_USER> /
<SCOPE_PASSWORD>, overridable in the config) gives direct read access to the
waveform data on its disk. This is orders of magnitude faster than
remote-command file transfer (TRFL etc.) and suits bulk retrieval of
sequence waveforms (.trc).

The transfer runs as "copy every file → verify each by hash → delete on
the scope only if every file verified" (2026-07 operating policy:
deleting to free disk space happens only after the copies are proven
good).

Safety measures:
  - SHA-256 is computed while copying, and after writing the PC-side file
    is re-read to verify hash and size (unverified files are never deleted)
  - files whose size changed between the start and end of the copy are
    treated as "being written" and reported as errors (protects against
    autosave races)
  - files modified too recently (default: within 15 s) are skipped
  - if even one file errors/conflicts/skips, the delete phase is not run
    at all (a re-run verifies already-copied files quickly)
  - a same-named file with different content on the PC is never
    overwritten; it is reported as a "conflict" (overwriting/renaming is
    a manual decision)
  - every result is logged to CSV (rel_path, size, sha256, status,
    deleted) as an audit trail of the deletions
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]

CHUNK = 1024 * 1024  # copy/hash block size

# Temporary files never transferred (in-progress fragments etc.)
SKIP_SUFFIXES = (".part", ".tmp")
# Windows metadata files never transferred (Explorer updates them when
# share settings change, which would trip the mtime guard and block the
# delete phase)
SKIP_NAMES = ("desktop.ini", "thumbs.db")


class TransferAborted(Exception):
    """User abort."""


def host_from_resource(resource: str) -> str:
    """Extract the host from a VISA resource name
    (VICP::<SCOPE_IP>::INSTR etc.)."""
    parts = resource.split("::")
    return parts[1] if len(parts) > 1 else resource


class ScopeFileMirror:
    """Retrieve (mirror) the scope's save directory to the PC.

    unc_root is the UNC path corresponding to scope_dir (the scope-side
    save base) **itself**. Example: if C:\\Users\\<SCOPE_WIN_USER>\\Desktop\\data
    is shared on the scope under the name "data", use
    "\\\\<SCOPE_IP>\\data". If empty, the administrative share is used
    ("\\\\<host>\\c$\\Users\\..."). Tests can pass a local directory to
    run without SMB.
    """

    def __init__(self, host: str, username: str, password: str,
                 scope_dir: str, dest_dir: str | Path,
                 unc_root: str = "", min_age_s: float = 15.0,
                 log: LogFunc = None):
        self.host = host
        self.username = username
        self.password = password
        self.scope_dir = scope_dir.rstrip("\\/")
        self.dest_dir = Path(dest_dir)
        self.min_age_s = min_age_s
        self._log = log or (lambda d, t: None)
        if unc_root:
            # a share mapping scope_dir directly (or a local test path)
            self.unc_base = unc_root.rstrip("\\/")
            self.share = self._share_root(self.unc_base)
        else:
            drive, sep, rest = self.scope_dir.partition(":")
            if not sep:
                raise ValueError(
                    f"Scope-side path has no drive letter: {scope_dir}")
            self.share = f"\\\\{host}\\{drive.lower()}$"
            self.unc_base = self.share + rest

    @staticmethod
    def _share_root(unc: str) -> str:
        """Extract the net-use target (\\\\host\\share) from a UNC path."""
        if not unc.startswith("\\\\"):
            return unc  # local path (for tests): net use is not involved
        parts = unc.strip("\\").split("\\")
        if len(parts) >= 2:
            return "\\\\" + parts[0] + "\\" + parts[1]
        return unc

    # ------------------------------------------------------------------
    # Connection (SMB authentication)
    # ------------------------------------------------------------------
    def ensure_connection(self) -> None:
        """Make the share accessible (authenticate via net use if
        needed)."""
        if os.path.isdir(self.unc_base):
            return
        if not self.share.startswith("\\\\") or not sys.platform.startswith("win"):
            raise RuntimeError(f"Source not found: {self.unc_base}")
        # A leftover session with different credentials fails with 1219,
        # so disconnect first
        subprocess.run(["net", "use", self.share, "/delete", "/y"],
                       capture_output=True, timeout=30)
        r = subprocess.run(
            ["net", "use", self.share, self.password,
             f"/user:{self.username}", "/persistent:no"],
            capture_output=True, timeout=30)
        out = (r.stdout + r.stderr).decode("cp932", errors="replace").strip()
        if r.returncode != 0:
            hint = self._net_use_hint(out)
            raise RuntimeError(
                f"Cannot connect to the scope share {self.share}: "
                f"{out}\n{hint}")
        self._log("INFO", f"SMB connected: {self.share}")
        if not os.path.isdir(self.unc_base):
            raise RuntimeError(
                f"Connected to share {self.share}, but the source "
                f"{self.unc_base} does not exist. Check that "
                "scope_save_dir / scope_smb_root correspond")

    @staticmethod
    def _net_use_hint(out: str) -> str:
        """Return a remedy for the error code found in net use output."""
        import re
        # \u30a8\u30e9\u30fc is the katakana word for "error" as printed
        # by net use on Japanese Windows (the lab PC); written as escapes
        # to keep this source ASCII-only
        m = re.search(r"(?:\u30a8\u30e9\u30fc|error)\s*(\d+)", out,
                      re.IGNORECASE)
        code = m.group(1) if m else ""
        if code == "5":
            return ("Remedy (access denied): the administrative share (c$) "
                    "is most likely restricted for remote local-admin "
                    "accounts. Either (a) add, in the scope's Windows "
                    "registry under "
                    "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
                    "Policies\\System, the DWORD "
                    "LocalAccountTokenFilterPolicy=1, or "
                    "(b) share the save folder on the scope and set that "
                    "share (e.g. \\\\<SCOPE_IP>\\data) as scope_smb_root "
                    "in config_local.json")
        if code in ("53", "67"):
            return ("Remedy (path not found): wrong share name, or SMB is "
                    "blocked. Check the share list with "
                    "net view \\\\<scope IP>. Old scopes (Windows XP "
                    "based) only speak SMB1, so you may need to enable the "
                    "'SMB 1.0/CIFS client' Windows feature on the PC")
        if code in ("86", "1326"):
            return ("Remedy (authentication failed): check user name / "
                    "password (scope_smb_user / scope_smb_password in the "
                    "config)")
        if code == "1219":
            return ("Remedy (credential conflict): a previous connection "
                    "is still open. Run: net use * /delete /y, then retry")
        return ("Check: (1) the scope is up and answers ping "
                "(2) user name / password (3) if the administrative share "
                "(c$) is unusable, share the save folder on the scope and "
                "set that share (e.g. \\\\<SCOPE_IP>\\data) as "
                "scope_smb_root")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _collect(self) -> list[tuple[str, int, float]]:
        """List of (relative path, size, mtime) to retrieve."""
        files = []
        for root, _dirs, names in os.walk(self.unc_base):
            for name in names:
                if name.lower().endswith(SKIP_SUFFIXES) \
                        or name.lower() in SKIP_NAMES:
                    continue
                path = os.path.join(root, name)
                st = os.stat(path)
                files.append((os.path.relpath(path, self.unc_base),
                              st.st_size, st.st_mtime))
        files.sort()
        return files

    @staticmethod
    def _sha256(path, abort=None) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                if abort is not None and abort():
                    raise TransferAborted()
                block = f.read(CHUNK)
                if not block:
                    break
                h.update(block)
        return h.hexdigest()

    def _copy_verify(self, src: str, dst: Path, abort=None) -> tuple[int, str]:
        """Copy src → dst and verify by re-reading the written file.

        Returns (size, sha256). Raises on verification failure (dst is
        removed).
        """
        size0 = os.path.getsize(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        h = hashlib.sha256()
        try:
            with open(src, "rb") as fs, open(tmp, "wb") as fd:
                while True:
                    if abort is not None and abort():
                        raise TransferAborted()
                    block = fs.read(CHUNK)
                    if not block:
                        break
                    h.update(block)
                    fd.write(block)
                fd.flush()
                os.fsync(fd.fileno())
            if os.path.getsize(src) != size0:
                raise RuntimeError(
                    "Scope-side size changed during copy (file may still "
                    "be written)")
            # verification: re-read the written file, compare hash & size
            if tmp.stat().st_size != size0:
                raise RuntimeError("Size mismatch after copy")
            dst_hash = self._sha256(tmp, abort)
            if dst_hash != h.hexdigest():
                raise RuntimeError("Hash mismatch after copy")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, dst)
        return size0, dst_hash

    # ------------------------------------------------------------------
    # Transfer body
    # ------------------------------------------------------------------
    def transfer(self, delete_after: bool = True, progress=None,
                 abort=None, log_csv: str | Path | None = None) -> dict:
        """Retrieve every file and, if verified (and requested), delete
        the scope-side originals.

        progress: callable(done, total, message)
        abort:    callable() -> bool (True aborts)
        Returns a summary dict (total/copied/already/errors/... /can_delete)
        """
        t0 = time.time()
        self.ensure_connection()
        if progress:
            progress(0, 0, "Listing files...")
        files = self._collect()
        total = len(files)
        records: list[dict] = []
        aborted = False
        copied_bytes = 0
        can_delete = False

        try:
            for i, (rel, size, mtime) in enumerate(files):
                if abort is not None and abort():
                    aborted = True
                    break
                if progress:
                    progress(i, total,
                             f"Transfer {i + 1}/{total}: {rel} "
                             f"({size / 1e6:.1f} MB)")
                src = os.path.join(self.unc_base, rel)
                dst = self.dest_dir / rel
                rec = {"rel": rel, "size": size, "sha256": "",
                       "status": "", "deleted": False, "note": ""}
                try:
                    if time.time() - mtime < self.min_age_s:
                        rec["status"] = "skipped_recent"
                        rec["note"] = ("modified too recently (may still "
                                       "be written)")
                    elif dst.exists():
                        # re-run (resume): identical content counts as
                        # already retrieved
                        src_hash = self._sha256(src, abort)
                        if dst.stat().st_size == size \
                                and self._sha256(dst, abort) == src_hash:
                            rec["sha256"] = src_hash
                            rec["status"] = "already"
                        else:
                            rec["status"] = "conflict"
                            rec["note"] = ("same-named file with different "
                                           "content on the PC")
                    else:
                        sz, dst_hash = self._copy_verify(src, dst, abort)
                        rec["sha256"] = dst_hash
                        rec["status"] = "copied"
                        copied_bytes += sz
                except TransferAborted:
                    rec["status"] = "aborted"
                    records.append(rec)
                    aborted = True
                    break
                except Exception as exc:
                    rec["status"] = "error"
                    rec["note"] = str(exc)
                    self._log("ERR", f"transfer failed {rel}: {exc}")
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
                    src = os.path.join(self.unc_base, r["rel"])
                    dst = self.dest_dir / r["rel"]
                    # final check of the copy's existence and size right
                    # before deleting
                    if not (dst.is_file() and dst.stat().st_size == r["size"]):
                        r["note"] += (" delete canceled: final check of "
                                      "the PC-side file failed")
                        continue
                    try:
                        os.remove(src)
                        r["deleted"] = True
                    except Exception as exc:
                        r["note"] += f" delete failed: {exc}"
                # clean up emptied sub-folders (keep the base)
                base = os.path.normpath(self.unc_base)
                for root, _dirs, _names in os.walk(self.unc_base,
                                                   topdown=False):
                    if os.path.normpath(root) == base:
                        continue
                    try:
                        os.rmdir(root)  # OSError if not empty → keep it
                    except OSError:
                        pass
        finally:
            if log_csv is not None:
                self._write_log_csv(log_csv, records)

        def count(s):
            return sum(1 for r in records if r["status"] == s)

        return {
            "total": total,
            "copied": count("copied"),
            "already": count("already"),
            "conflicts": count("conflict"),
            "errors": count("error"),
            "skipped": count("skipped_recent"),
            "deleted": sum(1 for r in records if r["deleted"]),
            "aborted": aborted,
            "can_delete": can_delete,
            "bytes": copied_bytes,
            "seconds": time.time() - t0,
            "records": records,
        }

    def _write_log_csv(self, path: str | Path, records: list[dict]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# scope→PC data retrieval log "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# src={self.unc_base} dest={self.dest_dir}"])
            w.writerow(["rel_path", "size_bytes", "sha256", "status",
                        "deleted", "note"])
            for r in records:
                w.writerow([r["rel"], r["size"], r["sha256"], r["status"],
                            "yes" if r["deleted"] else "no", r["note"]])
