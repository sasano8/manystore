# Tech Context

## 使用技術 / スタック

| 領域 | 技術 | 備考 |
|------|------|------|
| 言語 | Python | **3.14+ 前提**（`requires-python = ">=3.14"`）。3.14 は注釈遅延評価が既定＝`from __future__ import annotations` 不要 |
| パッケージ管理 | uv | `uv.lock` で pin。`uv sync` / `uv run` |
| ビルド | hatchling | wheel packages = `["manystore"]` |
| Lint/Format | ruff | `line-length=100`、`target-version="py314"`、`select=["E","F","I","UP","B","SIM"]`。`make`/CI は `RUFF_VERSION=0.15.18`（py314 対応版が必須） |
| テスト | pytest | `testpaths=["tests"]`、`addopts="-ra"` |
| CI | GitHub Actions | `.github/workflows/ci.yml`：push/PR で setup-uv → `make check` |
| 実 backend（任意） | docker-compose | `docker-compose.yml`（nats / minio 等）で実疎通検証用 |

## 開発セットアップ

```bash
uv sync          # 依存を解決（.venv）
```

## 検証コマンド（VERIFY 用）

> 品質チェックは**組織の品質方針**に従う（一般メソッド・チェックシートは [[quality]]）。本 repo はそれを
> **`make check`** に materialize 済み（Makefile が ruff 版を固定）。生 `uvx ruff …` のベタ書きはしない＝再現性。

**検証は Makefile 経由で叩く**（呼び出しは下記）。

```bash
make lint          # = uvx ruff@<固定版> check（lint のみ）
make format-check  # = ruff format --check + ruff check（書き換えない）
make test          # = uv run pytest
make check         # 一括（format-check + test）＝コミット前の「検証緑」判定はこれ
```

ワンショットで特定テストだけ回す等は `uv run pytest tests/ui -q` のように直接叩いてよいが、
**「検証緑か」の最終判定は `make check`** で行う（CI も同じターゲットを呼ぶ＝ローカルと一致）。

実 backend（S3/NATS）の疎通を確認するときは `docker-compose up -d` でバックエンドを起動してから
該当テストを実行する（接続情報が無い等で起動できない場合のみスキップし、その旨を activeContext に残す）。

## 技術的制約 / 依存

- 実行依存（`[project.dependencies]`）：
  `redis>=5.0.0` / `nats-py>=2.0.0` / `aiobotocore>=2.0.0` / `httpx>=0.27.0`。
  - `nats-py`=NATS / `aiobotocore`=S3 / `httpx`=**HTTP backend**（M018）/ local=stdlib。
  - `redis` は未使用（juice 抽出残骸。M005 で削除予定）。
- **optional extra `[server]`**（M019・UI/サーバ層）：`fastapi` / `uvicorn` / `watchdog`。
  `manystore.server` 内で遅延 import＝未導入でも `import manystore` は壊れない。`pip install "manystore[server]"`。
  起動: `python -m manystore.server --config <toml>`（既定 bind 127.0.0.1）。
- dev group: `pytest>=8.0` ＋ `pytest-asyncio>=0.24`（`asyncio_mode="auto"`＝`async def test_*` を自動非同期実行。
  既存の `asyncio.run` スタイルとも共存）＋ server テスト用に `fastapi`/`uvicorn`/`watchdog`（TestClient は httpx 依存）。
- import 名・プロジェクト名ともに `manystore`（旧 `shoudou_storage` から統一済み）。
- `SafeKeyValueStore.download` のキャッシュ既定先はローカル FS（`~/.cache/...`）。
