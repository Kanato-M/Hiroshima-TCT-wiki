# -*- coding: utf-8 -*-
"""オシロスコープ本体ディスクからのデータ回収 (SMB ファイル共有経由)。

LeCroy WavePro は Windows ベースのため、管理共有 (既定 C$) にオシロの
Windows アカウント (既定 <SCOPE_USER> / <SCOPE_PASSWORD>, config で上書き可) で
接続すれば本体ディスクの波形データを直接読み出せる。リモートコマンド
(TRFL 等) によるファイル転送より桁違いに速く、シーケンス波形 (.trc) の
大量回収に向く。

転送は「全ファイルをコピー → 1 ファイルずつハッシュ検証 → 全件成功の
場合に限りオシロ側を削除」の順で行う (容量確保のための削除は、コピーが
確実に成功したことを検証できた場合のみ、という 2026-07 運用方針)。

安全対策:
  - コピー中に SHA-256 を計算し、書き込み後に PC 側ファイルを読み直して
    ハッシュとサイズの一致を検証する (検証できないファイルは削除しない)
  - コピー前後でオシロ側のサイズが変わったファイルは「書き込み中」と
    みなしてエラー扱い (オートセーブ中の取りこぼし防止)
  - 更新時刻が新しすぎるファイル (既定 15 秒以内) はスキップ
  - 1 件でも エラー/競合/スキップ があれば削除フェーズ自体を行わない
    (再実行すればコピー済みファイルは高速に検証だけ行われる)
  - PC 側に同名で内容の異なるファイルがある場合は上書きせず「競合」として
    報告する (上書き・改名は手動で判断する)
  - 全件の結果を CSV (rel_path, size, sha256, status, deleted) に記録し、
    削除の証跡を残す
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

CHUNK = 1024 * 1024  # コピー/ハッシュのブロックサイズ

# 転送しない一時ファイル (書き込み途中の断片など)
SKIP_SUFFIXES = (".part", ".tmp")
# 転送しない Windows メタデータファイル (共有設定等で Explorer が更新し、
# mtime ガードに引っかかって削除フェーズをブロックするため除外する)
SKIP_NAMES = ("desktop.ini", "thumbs.db")


class TransferAborted(Exception):
    """ユーザー中断。"""


def host_from_resource(resource: str) -> str:
    """VISA リソース名 (VICP::<SCOPE_IP>::INSTR 等) からホストを取り出す。"""
    parts = resource.split("::")
    return parts[1] if len(parts) > 1 else resource


class ScopeFileMirror:
    """オシロ本体の保存ディレクトリを PC 側へ回収 (ミラー) する。

    unc_root は scope_dir (オシロ側の保存ベース) **そのもの**に対応する
    UNC パス。例: オシロ側で C:\\Users\\<SCOPE_WIN_USER>\\Desktop\\data を
    共有名 "data" で共有した場合は "\\\\<SCOPE_IP>\\data"。
    空なら管理共有からたどる ("\\\\<host>\\c$\\Users\\...")。
    テストではローカルディレクトリを渡せば SMB なしで動作する。
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
            # scope_dir に直接対応する共有 (またはテスト用ローカルパス)
            self.unc_base = unc_root.rstrip("\\/")
            self.share = self._share_root(self.unc_base)
        else:
            drive, sep, rest = self.scope_dir.partition(":")
            if not sep:
                raise ValueError(
                    f"オシロ側パスにドライブがありません: {scope_dir}")
            self.share = f"\\\\{host}\\{drive.lower()}$"
            self.unc_base = self.share + rest

    @staticmethod
    def _share_root(unc: str) -> str:
        """UNC パスから net use の対象 (\\\\host\\share) を取り出す。"""
        if not unc.startswith("\\\\"):
            return unc  # ローカルパス (テスト用): net use は使われない
        parts = unc.strip("\\").split("\\")
        if len(parts) >= 2:
            return "\\\\" + parts[0] + "\\" + parts[1]
        return unc

    # ------------------------------------------------------------------
    # 接続 (SMB 認証)
    # ------------------------------------------------------------------
    def ensure_connection(self) -> None:
        """共有へアクセスできるようにする (必要なら net use で認証)。"""
        if os.path.isdir(self.unc_base):
            return
        if not self.share.startswith("\\\\") or not sys.platform.startswith("win"):
            raise RuntimeError(f"回収元が見つかりません: {self.unc_base}")
        # 既存の別資格のセッションが残っていると 1219 で失敗するため一度切る
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
                f"オシロの共有 {self.share} に接続できません: {out}\n{hint}")
        self._log("INFO", f"SMB 接続: {self.share}")
        if not os.path.isdir(self.unc_base):
            raise RuntimeError(
                f"共有 {self.share} には接続できましたが回収元 "
                f"{self.unc_base} がありません。scope_save_dir / "
                "scope_smb_root の対応を確認してください")

    @staticmethod
    def _net_use_hint(out: str) -> str:
        """net use のエラー出力からエラーコード別の対処を返す。"""
        import re
        m = re.search(r"(?:エラー|error)\s*(\d+)", out, re.IGNORECASE)
        code = m.group(1) if m else ""
        if code == "5":
            return ("対処 (アクセス拒否): 管理共有 (c$) がリモートの一般"
                    "管理者アカウントに制限されている可能性が高い。"
                    "(a) オシロ側 Windows のレジストリに "
                    "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
                    "Policies\\System に DWORD "
                    "LocalAccountTokenFilterPolicy=1 を追加する、または "
                    "(b) オシロ側で保存フォルダを共有し config_local.json "
                    "の scope_smb_root にその共有 "
                    "(例 \\\\<SCOPE_IP>\\data) を指定する")
        if code in ("53", "67"):
            return ("対処 (パスが見つからない): 共有名の誤り、または SMB "
                    "がブロックされている。net view \\\\<オシロIP> で共有"
                    "一覧を確認。古いオシロ (Windows XP ベース) は SMB1 "
                    "のみ対応のため、PC 側で「Windows の機能」から "
                    "SMB 1.0 クライアントの有効化が必要な場合がある")
        if code in ("86", "1326"):
            return ("対処 (認証失敗): ユーザー名/パスワードを確認 "
                    "(config の scope_smb_user / scope_smb_password)")
        if code == "1219":
            return ("対処 (資格情報の競合): 既存の接続が残っている。"
                    "net use * /delete /y を実行してから再試行する")
        return ("確認: (1) オシロが起動していて ping が通るか "
                "(2) ユーザー名/パスワード (3) 管理共有 (c$) が使えない"
                "場合はオシロ側で保存フォルダを共有し scope_smb_root に"
                "その共有 (例 \\\\<SCOPE_IP>\\data) を指定する")

    # ------------------------------------------------------------------
    # 内部ヘルパ
    # ------------------------------------------------------------------
    def _collect(self) -> list[tuple[str, int, float]]:
        """回収対象の (相対パス, サイズ, mtime) 一覧。"""
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
        """src → dst をコピーし、書き込み結果を読み直して検証する。

        戻り値は (サイズ, sha256)。検証に失敗したら例外 (dst は残さない)。
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
                    "コピー中にオシロ側のサイズが変化 (書き込み中の可能性)")
            # 検証: 書いたファイルを読み直してハッシュ・サイズを照合
            if tmp.stat().st_size != size0:
                raise RuntimeError("コピー後のサイズが一致しません")
            dst_hash = self._sha256(tmp, abort)
            if dst_hash != h.hexdigest():
                raise RuntimeError("コピー後のハッシュが一致しません")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, dst)
        return size0, dst_hash

    # ------------------------------------------------------------------
    # 転送本体
    # ------------------------------------------------------------------
    def transfer(self, delete_after: bool = True, progress=None,
                 abort=None, log_csv: str | Path | None = None) -> dict:
        """全ファイルを回収し、検証済みなら (要求時) オシロ側を削除する。

        progress: callable(done, total, message)
        abort:    callable() -> bool (True で中断)
        戻り値: 集計 dict (total/copied/already/errors/... /can_delete)
        """
        t0 = time.time()
        self.ensure_connection()
        if progress:
            progress(0, 0, "ファイル一覧を取得中...")
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
                             f"転送 {i + 1}/{total}: {rel} ({size / 1e6:.1f} MB)")
                src = os.path.join(self.unc_base, rel)
                dst = self.dest_dir / rel
                rec = {"rel": rel, "size": size, "sha256": "",
                       "status": "", "deleted": False, "note": ""}
                try:
                    if time.time() - mtime < self.min_age_s:
                        rec["status"] = "skipped_recent"
                        rec["note"] = "更新が新しすぎる (書き込み中の可能性)"
                    elif dst.exists():
                        # 再実行 (レジューム): 同一内容なら回収済み扱い
                        src_hash = self._sha256(src, abort)
                        if dst.stat().st_size == size \
                                and self._sha256(dst, abort) == src_hash:
                            rec["sha256"] = src_hash
                            rec["status"] = "already"
                        else:
                            rec["status"] = "conflict"
                            rec["note"] = "PC側に内容の異なる同名ファイル"
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
                    self._log("ERR", f"転送失敗 {rel}: {exc}")
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
                    src = os.path.join(self.unc_base, r["rel"])
                    dst = self.dest_dir / r["rel"]
                    # 削除直前にコピー先の実在とサイズを最終確認する
                    if not (dst.is_file() and dst.stat().st_size == r["size"]):
                        r["note"] += " 削除中止: PC側ファイルの最終確認に失敗"
                        continue
                    try:
                        os.remove(src)
                        r["deleted"] = True
                    except Exception as exc:
                        r["note"] += f" 削除失敗: {exc}"
                # 空になったサブフォルダを片付ける (ベースは残す)
                base = os.path.normpath(self.unc_base)
                for root, _dirs, _names in os.walk(self.unc_base,
                                                   topdown=False):
                    if os.path.normpath(root) == base:
                        continue
                    try:
                        os.rmdir(root)  # 空でなければ OSError → 残す
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
            w.writerow([f"# オシロ→PC データ回収記録 "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# src={self.unc_base} dest={self.dest_dir}"])
            w.writerow(["rel_path", "size_bytes", "sha256", "status",
                        "deleted", "note"])
            for r in records:
                w.writerow([r["rel"], r["size"], r["sha256"], r["status"],
                            "yes" if r["deleted"] else "no", r["note"]])
