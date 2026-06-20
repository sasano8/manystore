# Tech Context

## 使用技術 / スタック

| 領域 | 技術 | 備考 |
|------|------|------|
| 言語 | Python | `requires-python = ">=3.10"`（pyproject）。開発環境は 3.14 |
| パッケージ管理 | uv | `uv.lock` で pin。`uv sync` / `uv run` |
| ビルド | hatchling | wheel packages = `["manystore"]` |
| Lint/Format | ruff | `line-length=100`、`select=["E","F","I","UP","B","SIM"]` |
| テスト | pytest | `testpaths=["tests"]`、`addopts="-ra"` |
| 実 backend（任意） | docker-compose | `docker-compose.yml`（nats / minio 等）で実疎通検証用 |

## 開発セットアップ

```bash
uv sync          # 依存を解決（.venv）
```

## 検証コマンド（VERIFY 用）

```bash
uvx ruff check manystore tests            # lint
uvx ruff format --check manystore tests   # format 確認
uv run pytest                             # test（現状 44 passed）
```

実 backend（S3/NATS）の疎通を確認するときは `docker-compose up -d` でバックエンドを起動してから
該当テストを実行する（接続情報が無い等で起動できない場合のみスキップし、その旨を activeContext に残す）。

## 技術的制約 / 依存

- 実行依存（`[project.dependencies]`、juice の dependency-group から移設）：
  `redis>=5.0.0` / `nats-py>=2.0.0` / `aiobotocore>=2.0.0` / `httpx>=0.27.0`。
- dev: `pytest>=8.0`。
- import 名・プロジェクト名ともに `manystore`（旧 `shoudou_storage` から統一済み）。
- `SafeKeyValueStore.download` のキャッシュ既定先はローカル FS（`~/.cache/...`）。
