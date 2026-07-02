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

> 品質チェックは**組織の品質方針**に従う（一般メソッド・チェックシートは [[unit-quality]]）。本 repo はそれを
> **`make check`** に materialize 済み（Makefile が ruff 版を固定）。生 `uvx ruff …` のベタ書きはしない＝再現性。

**検証は Makefile 経由で叩く**（呼び出しは下記）。

```bash
make format        # ruff format ＋ check --fix（書き換える）
make format-check  # ruff format --check + ruff check（書き換えない）
make test          # = uv run pytest -m "not slow"（fast・内ループ既定。M037）
make test-all      # = uv run pytest（slow 含む全部・CI 用）
make check         # 一括（format-check + test）＝コミット前の「検証緑」判定はこれ
```

> ⚠️ `make test`（fast）は **lint を回さない**＝format ドリフト（特に CJK 行の E501）は `make format` でしか
> 出ない。コミット前は `make format` も通すこと。

ワンショットで特定テストだけ回す等は `uv run pytest tests/ui -q` のように直接叩いてよいが、
**「検証緑か」の最終判定は `make check`** で行う（CI も同じターゲットを呼ぶ＝ローカルと一致）。

実 backend（S3/NATS）の疎通を確認するときは `docker-compose up -d` でバックエンドを起動してから
該当テストを実行する（接続情報が無い等で起動できない場合のみスキップし、その旨を activeContext に残す）。

## 技術的制約 / 依存

- 実行依存（`[project.dependencies]`）：
  `nats-py>=2.0.0` / `aiobotocore>=2.0.0` / `httpx>=0.27.0` / `anyio>=4.0.0`。
  - `nats-py`=NATS / `aiobotocore`=S3 / `httpx`=**HTTP backend**（M018）/ local=stdlib＋**anyio**。
  - `anyio`（M010）=local backend の同期 IO をスレッドへオフロード（`anyio.to_thread.run_sync`）。httpx 経由で
    在中だが直接使うので明示依存に格上げ。スレッドプール系＝真の async disk IO（aiofile/libaio）は不採用。
  - 未使用だった `redis` は M005 で削除済み。
- **optional extra `[server]`**（M019・UI/サーバ層）：`fastapi` / `uvicorn` / `watchdog`。
  `manystore.serving` 内で遅延 import＝未導入でも `import manystore` は壊れない。`pip install "manystore[server]"`。
  起動: `python -m manystore.serving.server --config <toml>`。
- **CLI（M075・Typer）**：`typer` は core 依存だが `import manystore` では読み込まれない（`__main__` 起動時のみ）。
  console script `manystore` ＋ `python -m manystore`。`manystore serve --config <toml>`（統合サーバ・既定 bind
  127.0.0.1）／`manystore store init [dir]`（`manystore.toml` 雛形）。旧 `python -m manystore --config` は
  先頭 `--config` 検出で serve に後方互換で振る。
- dev group: `pytest>=8.0` ＋ `pytest-asyncio>=0.24`（`asyncio_mode="auto"`＝`async def test_*` を自動非同期実行。
  既存の `asyncio.run` スタイルとも共存）＋ server テスト用に `fastapi`/`uvicorn`/`watchdog`（TestClient は httpx 依存）。
- import 名・プロジェクト名ともに `manystore`（旧 `shoudou_storage` から統一済み）。
- `SafeKeyValueStore.download` のキャッシュ既定先はローカル FS（`~/.cache/...`）。
