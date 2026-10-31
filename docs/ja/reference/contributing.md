# Wiki の編集方法

この Wiki は [MkDocs](https://www.mkdocs.org/) と [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) で作られています。
多言語化には [mkdocs-static-i18n](https://github.com/ultrabug/mkdocs-static-i18n) を使っています。

## ファイル構成

```text
.
├── mkdocs.yml              # サイト設定・ナビゲーション
├── requirements.txt        # 必要な Python パッケージ
├── docs/
│   ├── ja/                 # 日本語ページ（デフォルト言語）
│   ├── en/                 # 英語ページ
│   └── javascripts/        # 数式表示 (MathJax) の設定
└── .github/workflows/
    └── deploy.yml          # GitHub Pages への自動デプロイ
```

日本語と英語のページは、**同じ相対パス・同じファイル名** で対応させます。
例：`docs/ja/setup/hardware.md` ⇔ `docs/en/setup/hardware.md`

英語版のページがない場合は日本語版が表示されます。

## ローカルでのプレビュー

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/internal.py prepare   # 内部情報ページを用意（パスワードが無ければ案内ページ）
mkdocs serve
```

ブラウザで <http://127.0.0.1:8000/Hiroshima-TCT-wiki/> を開くと、編集内容がその場で反映されます。

## ページを追加する

1. `docs/ja/` と `docs/en/` の同じ場所に Markdown ファイルを作る。
2. `mkdocs.yml` の `nav` に日本語のタイトルで追加する。
3. 英語のタイトルを `mkdocs.yml` の `nav_translations` に追加する。
4. `mkdocs build --strict` でリンク切れなどの警告がないことを確認する。

## 公開の流れ

1. ブランチを作って編集し、プルリクエストを出す（CI で `mkdocs build --strict` が実行される）。
2. `main` ブランチにマージされると、GitHub Actions が自動でサイトをビルドして GitHub Pages に公開する。

!!! note "初回のみの設定"
    リポジトリの **Settings → Pages → Build and deployment → Source** を **GitHub Actions** に設定してください。

## 公開しない情報と内部情報ページ { #internal }

このサイトは誰でも見られます。**IP アドレス、NAS のアドレス、ユーザー名、パスワード、メールアドレス、個人名などは、公開ページに書かないでください。**
これらは [内部情報](../internal.md) ページにまとめ、公開ページからはそこへリンクします。

内部情報ページの仕組み：

- 平文の Markdown はリポジトリに入れず、パスワードで暗号化した `internal/ja.md.enc`・`internal/en.md.enc` だけをコミットします。
- GitHub Actions がビルド時に Secret `WIKI_INTERNAL_PASSWORD` で復号し、[mkdocs-encryptcontent-plugin](https://github.com/unverbuggt/mkdocs-encryptcontent-plugin) で
  同じパスワードを入力したときだけブラウザ上で表示されるページとして公開します（検索にも出ません）。
- Secret が設定されていないビルド（手元のプレビューなど）では、案内だけのページになります。

内部情報を編集するとき（パスワードを知っている人のみ）：

```bash
python scripts/internal.py decrypt   # internal/src/ja.md, en.md に平文を出す（.gitignore 済み）
# internal/src/*.md を編集する（日英両方）
python scripts/internal.py encrypt   # 暗号化して internal/*.md.enc を更新
git add internal/*.md.enc            # コミットするのは .enc だけ
```

パスワードを変えるときは `python scripts/internal.py rekey` を実行し、リポジトリの **Settings → Secrets and variables → Actions** の
`WIKI_INTERNAL_PASSWORD` も新しいパスワードに更新します。

!!! warning "注意"
    - `internal/src/` と `docs/*/internal.md` は平文なので、絶対にコミットしないでください（`.gitignore` で除外しています）。
    - ブラウザ側で復号する仕組みなので、強度はパスワードの強さで決まります。長いランダムなパスワードを使い、研究室外に教えないでください。

## 書き方のヒント

- 数式は `\( ... \)`（インライン）、`\[ ... \]`（ディスプレイ）で書けます。
- 注意書きは `!!! note`、`!!! warning`、`!!! danger` などの admonition を使います。
- 図は `mermaid` のコードブロックで描けます。
- 画像は各言語フォルダ内の `images/` などに置き、相対パスで参照してください。
