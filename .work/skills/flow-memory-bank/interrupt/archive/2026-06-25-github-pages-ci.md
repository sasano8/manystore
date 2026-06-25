# 要望: docs を GitHub Pages に CI でビルド・公開

- ユーザー要望（2026-06-25）: CI で GitHub Pages にビルドしたい。
- 決定（ユーザー確認済）:
  - 生成器 = **MkDocs Material**（uv 管理・最小依存）。
  - デプロイ = **公式 GitHub Pages Actions（actions/deploy-pages）/ main push のみ**公開、PR ではビルド検証のみ。
  - ビルド時に `make conformance-docs` で **conformance spec を再生成**してから公開（常に最新）。
- 実装: `docs` dependency-group に mkdocs-material 追加 / `mkdocs.yml` / `make docs`・`make docs-serve` /
  `.github/workflows/pages.yml`（regen→build→deploy）。
- 手動前提: リポジトリ Settings → Pages → Source = GitHub Actions を有効化（ユーザー作業）。
