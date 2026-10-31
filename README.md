# Hiroshima TCT Wiki

TCT (Transient Current Technique) scan の測定・解析に関する日本語・英語の Wiki です。
A bilingual (Japanese / English) wiki for TCT (Transient Current Technique) scan measurements and analysis.

- Site: <https://kanato-m.github.io/Hiroshima-TCT-wiki/>
- English: <https://kanato-m.github.io/Hiroshima-TCT-wiki/en/>

## ローカルでのプレビュー / Local preview

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdocs serve
```

<http://127.0.0.1:8000/Hiroshima-TCT-wiki/> を開いてください。 / Open <http://127.0.0.1:8000/Hiroshima-TCT-wiki/>.

## 構成 / Structure

| パス / Path | 内容 / Content |
| --- | --- |
| `docs/ja/` | 日本語ページ（デフォルト） / Japanese pages (default) |
| `docs/en/` | 英語ページ / English pages |
| `mkdocs.yml` | サイト設定・ナビゲーション / Site config and navigation |
| `.github/workflows/deploy.yml` | GitHub Pages への自動デプロイ / Automatic deployment to GitHub Pages |

## 公開 / Deployment

`main` ブランチへの push で GitHub Actions がサイトをビルドし、GitHub Pages に公開します。
初回のみ、リポジトリの **Settings → Pages → Source** を **GitHub Actions** に設定してください。

Pushing to `main` builds the site with GitHub Actions and publishes it to GitHub Pages.
For the first deployment, set **Settings → Pages → Source** to **GitHub Actions**.

詳しくは Wiki 内の「Wiki の編集方法」/ "Contributing" ページを参照してください。
