# manystore

差し替え可能なバックエンド（**local / S3 / NATS**）を共通インターフェイスの背後に隠す、ストレージ抽象ライブラリ。
利用側は接続情報を変えるだけで保存先を切り替えられる。

## 特徴

- **2 つのストア抽象**
  - `KeyValueStore` — `put` / `get` / `list` / `exists` / `delete` / `cp` / `mv`（バイト列をキーで出し入れ）
  - `FileStore` — `open` で `FileObject`（ストリーム）を取得
- **backend を差し替えるだけ** — `Local` / `S3` / `NATS Object Store`。ラッパは 1 枚に留め、下で backend を入れ替える。
- **async が一次実装**、sync ブリッジ（`AsyncToSyncKeyValueStore`）も提供。
- **接続ライフサイクル** — `connect` / retry / timeout / deadline を `ConnectPolicy` で制御。
- **安全パス** — `validate_safe_path` / `Safe*` ラッパが traversal などの危険な key/filename を既定で弾く。
- **アトミック書き込み** — local は temp+`os.replace`、S3/NATS は元々アトミック（all-or-nothing）。

## 必要環境

- **Python 3.14+**（`requires-python = ">=3.14"`）
- パッケージ管理は **uv**

```bash
uv sync
```

## クイックスタート（local backend）

```python
import asyncio
from pathlib import Path
from manystore import connect_key_value_store

async def main():
    async with connect_key_value_store("local", local_dir=Path("./data")) as store:
        await store.put("greeting.txt", b"hello")
        data = await store.get("greeting.txt")        # b"hello"
        print(await store.exists("greeting.txt"))     # True
        print(await store.list(limit=10))             # [{"filename": "greeting.txt", "size": 5}]
        await store.cp("greeting.txt", "copy.txt")
        await store.delete("greeting.txt")

asyncio.run(main())
```

`connect_key_value_store(backend, *, verify=True, policy=None, **opts)` は「接続前の状態」を返し、
`async with` で初めて接続する（`verify=True` なら接続失敗を送出、`False` なら無視して遅延接続に委ねる）。

## backend ごとの接続

backend 名と接続オプション（`**opts`）だけが違い、得られる `store` の使い方は同じ。

```python
# S3（minio / SeaweedFS 等の S3 互換も可）
async with connect_key_value_store(
    "s3",
    s3_bucket="my-bucket",
    s3_endpoint="http://localhost:8333",
    s3_region="us-east-1",
    s3_access_key="...",
    s3_secret_key="...",
    s3_addressing_style="path",   # S3 互換サーバ（minio/SeaweedFS 等）は path 必須。既定は "virtual"（ドメイン）
) as store:
    ...

# NATS Object Store
async with connect_key_value_store(
    "nats",
    nats_url="nats://localhost:4222",
    nats_bucket="manystore_files",
) as store:
    ...
```

接続を挟まず実体を直接作るなら `create_key_value_store(backend, **opts)` も使える。

## 接続ポリシー（ConnectPolicy）

初回 timeout・リトライ・指数バックオフ・全体 deadline をまとめて制御。プリセット 3 種:

```python
from manystore import ConnectPolicy, connect_key_value_store

ConnectPolicy.default()    # 既定。指数バックオフで deadline=60s まで粘る
ConnectPolicy.fail_fast()  # リトライせず短い timeout で 1 回だけ（到達性を素早く判定）
ConnectPolicy.forever()    # 依存サービスが起動するまで無期限に粘る

async with connect_key_value_store("nats", nats_url=..., policy=ConnectPolicy.fail_fast()) as store:
    ...
```

> `max_retry=inf` にするなら `deadline` を有限にしないと止まらない（既定は `deadline=60`）。

## 安全パス（Safe ラッパ）

任意の `KeyValueStore` / `FileStore` を 1 枚で包み、`validate_safe_path` で key/filename を検証してから委譲する。

```python
from manystore import SafeKeyValueStore, UnsafePathError

safe = SafeKeyValueStore(store)
await safe.put("a/b.txt", b"...")     # OK
await safe.put("../escape", b"...")   # UnsafePathError
```

## その他の公開 API

- `AsyncToSyncKeyValueStore` — async ストアを同期 IF（`SyncKeyValueStore`）として被せるゼロ依存ブリッジ。
- `ArrayKeyValueStore` — 論理名（キー先頭セグメント）で複数 backend を束ねる合成ストア。`DownloadCache` 付き。
- `KeyValueFileStore` — 任意の KVS を `FileStore` 化する汎用アダプタ。
- backend クラス直指定: `LocalKeyValueStore` / `S3KeyValueStore` / `NatsObjectKeyValueStore`（および各 `*FileStore`）。

公開シンボルの一覧は `manystore.__all__` を参照。

## 開発

```bash
uv sync
make format      # 整形（uvx で ruff をバージョン固定: RUFF_VERSION）
make check       # format 確認 + test（CI と同じ）
```

- Lint/Format は **ruff**（`target-version = "py314"`）。3.14 は注釈遅延評価が既定なので
  `from __future__ import annotations` は不要。
- テストは **pytest**（S3 / NATS は in-memory fake で検証）。
- CI は GitHub Actions（`.github/workflows/ci.yml`）で push / PR ごとに `make check`。
- 実 backend（minio / 実 NATS）疎通は `docker-compose.yml` を起動して検証する。
