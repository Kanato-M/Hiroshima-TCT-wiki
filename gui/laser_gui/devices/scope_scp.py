# -*- coding: utf-8 -*-
"""オシロスコープ本体ディスクからのデータ回収 (SSH/SFTP, scp 相当)。

旧 SMB 方式 (scope_files.py) からの仕様変更 (2026-09-07):
  - オシロ側の OpenSSH サーバへ SSH 接続し SFTP でファイルを取得する
    (ターミナルの `scp -r <SCOPE_USER>@<SCOPE_IP>:C:\\...\\20260907 .` と同じ
    プロトコル。最近の OpenSSH は scp コマンド自体が内部 SFTP)。
    SMB + SHA-256 再読込検証より大幅に速い。
  - パスワードは保存せず、転送のたびに GUI で入力する (scan_panel 側)。
  - 回収単位は「日付フォルダ」: <scope_base>\\<年>\\<YYYYMMDD> を丸ごと
    PC 側 <dest_base>\\<年>\\<YYYYMMDD> へ取得する。
  - 回収済みファイル (PC 側に同名・同サイズ) はスキップする。フォルダ内の
    全ファイルがスキップされたスキャンフォルダは「回収済み」として数える
    (重複フォルダのスキップ)。同名でサイズが違うものは上書きせず「競合」。
  - 検証はサイズ照合 (SFTP/TCP レベルの整合性 + 転送後の実サイズ確認)。
  - オシロ側削除 (任意) は「エラー・競合・中断が 1 件もない」場合のみ、
    サイズ検証済みのファイルを削除し、空になったサブフォルダを片付ける。

オシロ側の前提: Windows OpenSSH サーバが有効 (実機は 2026-09 時点で
scp 接続可を確認済み)。SFTP のリモートパスは "C:/Users/..." 形式
(バックスラッシュは自動変換する)。
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
    """ユーザー中断。"""


def host_from_resource(resource: str) -> str:
    """VISA リソース名 (VICP::<SCOPE_IP>::INSTR 等) からホストを取り出す。"""
    parts = resource.split("::")
    return parts[1] if len(parts) > 1 else resource


def _to_sftp_path(win_path: str) -> str:
    """Windows パス → SFTP パス (C:\\a\\b → C:/a/b)。"""
    return win_path.replace("\\", "/").rstrip("/")


class ScpFetcher:
    """SSH/SFTP でオシロの日付フォルダを PC へ回収する。

    使い方 (GUI からはワーカースレッド経由):
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
            # LAN 内転送のスループット向上 (既定 2 MB → 4 MB 窓)
            t.default_window_size = 4 * 1024 * 1024
            t.banner_timeout = self.timeout_s
            t.connect(username=self.username, password=self.password)
            self._transport = t
            self._sftp = paramiko.SFTPClient.from_transport(t)
        except paramiko.AuthenticationException:
            self.close()
            raise RuntimeError(
                f"SSH 認証に失敗しました ({self.username}@{self.host})。"
                "パスワードを確認してください") from None
        except OSError as exc:
            self.close()
            raise RuntimeError(
                f"オシロ {self.host}:{self.port} に SSH 接続できません: "
                f"{exc}\n確認: (1) オシロが起動していて ping が通るか "
                "(2) オシロ側の OpenSSH サーバ (sshd) が動作しているか")
        self._log("INFO", f"SSH 接続: {self.username}@{self.host}")

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
        """リモート top 以下の全ファイル (相対パス, サイズ) を列挙する。"""
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
        """<scope_base>/<年>/<date_str> を丸ごと回収する。

        progress: callable(done, total, message)
        abort:    callable() -> bool (True で中断)
        戻り値: 集計 dict (total/copied/already/conflicts/errors/deleted/
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
                    f"オシロ側に回収元がありません: {remote_top}")

            if progress:
                progress(0, 0, "ファイル一覧を取得中...")
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
                             f"回収 {i + 1}/{total}: {rel} "
                             f"({size / 1e6:.1f} MB)")
                try:
                    if dst.exists():
                        if dst.stat().st_size == size:
                            rec["status"] = "already"   # 回収済み → スキップ
                        else:
                            rec["status"] = "conflict"
                            rec["note"] = "PC側に別サイズの同名ファイル"
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
                                    f"転送後サイズ不一致 "
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
                    self._log("ERR", f"回収失敗 {rel}: {exc}")
                records.append(rec)

            # --- 削除フェーズ: 全件が検証済み (copied/already) の場合のみ ---
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
                        r["note"] += " 削除中止: PC側の最終確認に失敗"
                        continue
                    try:
                        self._sftp.remove(f"{remote_top}/{r['rel']}")
                        r["deleted"] = True
                    except Exception as exc:
                        r["note"] += f" 削除失敗: {exc}"
                # 空になったフォルダを日付フォルダごと片付ける (深い順)
                dirs = sorted({r["rel"].rsplit("/", 1)[0]
                               for r in records if "/" in r["rel"]},
                              key=len, reverse=True)
                for d in dirs + [""]:
                    try:
                        self._sftp.rmdir(f"{remote_top}/{d}" if d
                                         else remote_top)
                    except Exception:
                        pass  # 空でなければ残す
        finally:
            self.close()
            if log_csv is not None:
                self._write_log_csv(log_csv, remote_top, dest_top, records)

        def count(s):
            return sum(1 for r in records if r["status"] == s)

        # 「重複フォルダのスキップ」集計: 全ファイルが回収済みだった
        # サブフォルダ (スキャンフォルダ) の数
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
            w.writerow([f"# オシロ→PC データ回収記録 (SSH/SFTP) "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# src={remote_top} dest={dest_top}"])
            w.writerow(["rel_path", "size_bytes", "status", "deleted",
                        "note"])
            for r in records:
                w.writerow([r["rel"], r["size"], r["status"],
                            "yes" if r["deleted"] else "no", r["note"]])
