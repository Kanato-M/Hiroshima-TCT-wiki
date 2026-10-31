#!/usr/bin/env python3
"""内部情報ページ（パスワード保護）の管理スクリプト / Manage the password-protected internal page.

内部情報（IP アドレス、アカウント、連絡先など）の Markdown は、平文ではリポジトリに入れず、
パスワードで暗号化した `internal/<lang>.md.enc` だけをコミットします。
サイトのビルド時に復号し、mkdocs-encryptcontent-plugin で同じパスワードを使って
ブラウザ側で復号するページとして出力します。

The plaintext Markdown never enters the repository; only `internal/<lang>.md.enc`
(encrypted with the password) is committed. At build time it is decrypted and emitted as a
page that the browser decrypts with the same password (mkdocs-encryptcontent-plugin).

Usage:
    python scripts/internal.py decrypt   # internal/<lang>.md.enc -> internal/src/<lang>.md (編集用 / for editing)
    python scripts/internal.py encrypt   # internal/src/<lang>.md -> internal/<lang>.md.enc
    python scripts/internal.py prepare   # ビルド前に docs/<lang>/internal.md を生成 / generate docs/<lang>/internal.md
    python scripts/internal.py rekey     # パスワードを変更 / change the password

パスワードは環境変数 WIKI_INTERNAL_PASSWORD から読みます。無ければ入力を求めます
（prepare だけは、無ければ「パスワードなしのビルド」用の案内ページを作ります）。
The password is read from WIKI_INTERNAL_PASSWORD, or prompted for. `prepare` writes a
placeholder page instead when no password is available.
"""
from __future__ import annotations

import base64
import json
import getpass
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parent.parent
ENC_DIR = ROOT / "internal"
SRC_DIR = ENC_DIR / "src"
DOCS = ROOT / "docs"
LANGS = ("ja", "en")
ENV = "WIKI_INTERNAL_PASSWORD"
ITERATIONS = 600_000

# 公開ページからのリンク先アンカー（プレースホルダーにも置いてリンク切れを防ぐ）
# Anchors linked from public pages; also present in the placeholder so links stay valid.
ANCHORS = ("contacts", "network", "accounts", "config", "nas", "scope-scp")

PLACEHOLDER = {
    "ja": (
        "# 内部情報\n\n"
        "!!! info \"このビルドには内部情報が含まれていません\"\n"
        "    内部情報は、パスワードを設定したビルド（公開サイト）でのみ、パスワード付きで表示されます。\n"
    ),
    "en": (
        "# Internal information\n\n"
        "!!! info \"This build does not include the internal information\"\n"
        "    The internal information is only shown, behind a password, in builds that have the password set (the public site).\n"
    ),
}


def _key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_text(text: str, password: str) -> bytes:
    salt = os.urandom(16)
    token = Fernet(_key(password, salt)).encrypt(text.encode("utf-8"))
    return base64.b64encode(salt) + b"\n" + token + b"\n"


def decrypt_bytes(data: bytes, password: str) -> str:
    salt_b64, token = data.strip().split(b"\n", 1)
    try:
        return Fernet(_key(password, base64.b64decode(salt_b64))).decrypt(token.strip()).decode("utf-8")
    except InvalidToken:
        sys.exit("パスワードが違います / wrong password")


def get_password(prompt: str = "Password: ", allow_missing: bool = False) -> str | None:
    pw = os.environ.get(ENV)
    if pw:
        return pw
    if allow_missing:
        return None
    return getpass.getpass(prompt)


def cmd_encrypt(password: str) -> None:
    for lang in LANGS:
        src = SRC_DIR / f"{lang}.md"
        if not src.exists():
            sys.exit(f"{src} がありません / missing")
        (ENC_DIR / f"{lang}.md.enc").write_bytes(encrypt_text(src.read_text(encoding="utf-8"), password))
        print(f"encrypted {src.relative_to(ROOT)} -> internal/{lang}.md.enc")


def cmd_decrypt(password: str) -> None:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    for lang in LANGS:
        text = decrypt_bytes((ENC_DIR / f"{lang}.md.enc").read_bytes(), password)
        (SRC_DIR / f"{lang}.md").write_text(text, encoding="utf-8")
        print(f"decrypted internal/{lang}.md.enc -> {(SRC_DIR / f'{lang}.md').relative_to(ROOT)}")


def cmd_prepare(password: str | None) -> None:
    for lang in LANGS:
        out = DOCS / lang / "internal.md"
        if password:
            body = decrypt_bytes((ENC_DIR / f"{lang}.md.enc").read_bytes(), password)
            # ページ単位で暗号化し、検索インデックスからも外す / encrypt this page and keep it out of search
            front = f"---\npassword: {json.dumps(password)}\nsearch:\n  exclude: true\nhide:\n  - toc\n---\n\n"
            out.write_text(front + body, encoding="utf-8")
            print(f"prepared {out.relative_to(ROOT)} (encrypted page)")
        else:
            anchors = "".join(f'<span id="{a}"></span>' for a in ANCHORS)
            out.write_text(PLACEHOLDER[lang] + "\n" + anchors + "\n", encoding="utf-8")
            print(f"prepared {out.relative_to(ROOT)} (placeholder, no password)")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "encrypt":
        pw = get_password("新しいパスワード / Password: ")
        if not os.environ.get(ENV) and getpass.getpass("確認 / Confirm: ") != pw:
            sys.exit("パスワードが一致しません / passwords do not match")
        cmd_encrypt(pw)
    elif cmd == "decrypt":
        cmd_decrypt(get_password())
    elif cmd == "prepare":
        cmd_prepare(get_password(allow_missing=True))
    elif cmd == "rekey":
        old = getpass.getpass("今のパスワード / Current password: ")
        cmd_decrypt(old)
        new = getpass.getpass("新しいパスワード / New password: ")
        if getpass.getpass("確認 / Confirm: ") != new:
            sys.exit("パスワードが一致しません / passwords do not match")
        cmd_encrypt(new)
        print("GitHub の Secret WIKI_INTERNAL_PASSWORD も新しいパスワードに更新してください / update the GitHub secret too")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
