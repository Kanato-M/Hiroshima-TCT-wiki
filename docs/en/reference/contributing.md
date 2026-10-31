# Contributing

This wiki is built with [MkDocs](https://www.mkdocs.org/) and [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/).
Translations are handled by [mkdocs-static-i18n](https://github.com/ultrabug/mkdocs-static-i18n).

## File layout

```text
.
├── mkdocs.yml              # site settings and navigation
├── requirements.txt        # required Python packages
├── docs/
│   ├── ja/                 # Japanese pages (default language)
│   ├── en/                 # English pages
│   └── javascripts/        # math rendering (MathJax) settings
└── .github/workflows/
    └── deploy.yml          # automatic deployment to GitHub Pages
```

Japanese and English pages are matched by **the same relative path and file name**.
Example: `docs/ja/setup/hardware.md` ⇔ `docs/en/setup/hardware.md`

If an English page is missing, the Japanese page is shown instead.

## Local preview

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/internal.py prepare   # prepare the internal page (placeholder without the password)
mkdocs serve
```

Open <http://127.0.0.1:8000/Hiroshima-TCT-wiki/> in a browser. Edits are reflected immediately.

## Adding a page

1. Create the Markdown file at the same location in both `docs/ja/` and `docs/en/`.
2. Add it to `nav` in `mkdocs.yml` with its Japanese title.
3. Add the English title to `nav_translations` in `mkdocs.yml`.
4. Run `mkdocs build --strict` and make sure there are no warnings such as broken links.

## Publishing

1. Create a branch, edit, and open a pull request (CI runs `mkdocs build --strict`).
2. When it is merged into `main`, GitHub Actions builds the site and publishes it to GitHub Pages.

!!! note "One-time setup"
    Set **Settings → Pages → Build and deployment → Source** to **GitHub Actions** in the repository.

## Information not to publish, and the internal page { #internal }

This site is visible to anyone. **Do not write IP addresses, the NAS address, user names, passwords, e-mail addresses or personal names on public pages.**
Put them on the [Internal information](../internal.md) page and link to it from public pages.

How the internal page works:

- The plaintext Markdown is never committed; only `internal/ja.md.enc` and `internal/en.md.enc`, encrypted with a password, are in the repository.
- At build time GitHub Actions decrypts them with the secret `WIKI_INTERNAL_PASSWORD` and publishes them with
  [mkdocs-encryptcontent-plugin](https://github.com/unverbuggt/mkdocs-encryptcontent-plugin) as a page that the browser shows only after the same password is entered (it is not searchable either).
- Builds without the secret (e.g. a local preview) get a placeholder page.

To edit the internal information (only people who know the password):

```bash
python scripts/internal.py decrypt   # writes the plaintext to internal/src/ja.md, en.md (git-ignored)
# edit internal/src/*.md (both languages)
python scripts/internal.py encrypt   # re-encrypts into internal/*.md.enc
git add internal/*.md.enc            # commit only the .enc files
```

To change the password, run `python scripts/internal.py rekey` and also update `WIKI_INTERNAL_PASSWORD` under the repository's
**Settings → Secrets and variables → Actions**.

!!! warning "Caution"
    - `internal/src/` and `docs/*/internal.md` are plaintext; never commit them (they are excluded by `.gitignore`).
    - Decryption happens in the browser, so the protection is only as strong as the password. Use a long random password and do not share it outside the lab.

## Writing tips

- Write math as `\( ... \)` (inline) or `\[ ... \]` (display).
- Use admonitions such as `!!! note`, `!!! warning` and `!!! danger` for callouts.
- Draw diagrams with `mermaid` code blocks.
- Put images in a folder such as `images/` inside each language folder and reference them with relative paths.
