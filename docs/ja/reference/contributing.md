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

## 書き方のヒント

- 数式は `\( ... \)`（インライン）、`\[ ... \]`（ディスプレイ）で書けます。
- 注意書きは `!!! note`、`!!! warning`、`!!! danger` などの admonition を使います。
- 図は `mermaid` のコードブロックで描けます。
- 画像は各言語フォルダ内の `images/` などに置き、相対パスで参照してください。
