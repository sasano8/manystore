# manystore

差し替え可能なバックエンド（**local / S3 / NATS / HTTP**）を共通インターフェイスの背後に隠す、ストレージ抽象ライブラリ。
利用側は接続情報を変えるだけで保存先を切り替えられる。

## 特徴

- **1 つの Store**（`AsyncStore`。トップ `manystore` から再エクスポート）＝値 API と IO API を同じ面に持つ:
  - **値 API** — `put` / `get` / `get_or_raise` / `list_all` / `exists` / `delete` / `cp` / `mv`（バイト列をキーで出し入れ）
  - **IO API** — `open_reader` / `open_writer` で `FileObject`（ストリーム・**バイナリ専用**）を取得
  - put/get だけ見たい呼び出し側向けに、狭い view 型 `AsyncBufferedStore`（sync は `SyncStore`）も公開する。
- **backend を差し替えるだけ** — `Local` / `S3` / `NATS Object Store` / `HTTP`（read-only）。ラッパは 1 枚に留め、下で backend を入れ替える。
- **async が一次実装**、sync ブリッジ（`AsyncToSyncStore`）も提供。
- **接続ライフサイクル** — `connect` / retry / timeout / deadline を `ConnectPolicy` で制御。
- **安全パス** — `validate_safe_path` / `SafeStore` ラッパが traversal などの危険な key/filename を既定で弾く。
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
from manystore import open_async_store

async def main():
    async with open_async_store("local", local_dir=Path("./data")) as store:
        await store.put("greeting.txt", b"hello")
        data = await store.get("greeting.txt")        # b"hello"
        print(await store.exists("greeting.txt"))     # True
        print(await store.list_all(limit=10))         # [{"filename": "greeting.txt", "size": 5}]（全キー平坦）
        await store.cp("greeting.txt", "copy.txt")
        await store.delete("greeting.txt")

asyncio.run(main())
```

`open_async_store(backend, *, verify=True, policy=None, **opts)` はライブラリの**顔**＝**Safe 包装込みの接続 CM**。
「接続前の状態」を返し、`async with` で初めて接続する（`verify=True` なら接続失敗を送出、`False` なら無視して
遅延接続に委ねる）。返る `store` は値 API も IO API も持つ 1 つの Store。URL や構成ファイルの context 名から開くなら
`open_store("s3://bucket?…")` / `open_store("mycontext")`。

## backend ごとの接続

backend 名と接続オプション（`**opts`）だけが違い、得られる `store` の使い方は同じ。

```python
# S3（minio / SeaweedFS 等の S3 互換も可）
async with open_async_store(
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
async with open_async_store(
    "nats",
    nats_url="nats://localhost:4222",
    nats_bucket="manystore_files",
) as store:
    ...

# HTTP（read-only。GET/HEAD で取得するだけ。書き込み・一覧は非対応）
async with open_async_store(
    "http",
    http_base_url="https://example.com/files",
    http_headers={"Authorization": "Bearer ..."},  # 任意（認証等）
) as store:
    data = await store.get("a.txt")     # base_url + "/a.txt" を GET（404 は None）
    exists = await store.exists("a.txt")  # HEAD で存在確認
```

> **HTTP backend は read-only**: `get` / `exists` と `open_reader(...)` のみ。`put` / `delete` /
> `cp` / `mv` / `list_all` / `iter_all` / `open_writer` は `io.UnsupportedOperation` を投げる。

Safe 包装のみ（接続は自前）で欲しいなら `create_safe_store(backend, **opts)`（未接続）、Safe 無しで接続だけ挟むなら
`connect_store(backend, **opts)`（生・キー検証なし）、生の実体を直接作るなら `create_unsafe_store(backend, **opts)`。

## 接続ポリシー（ConnectPolicy）

初回 timeout・リトライ・指数バックオフ・全体 deadline をまとめて制御。プリセット 3 種:

```python
from manystore import ConnectPolicy, open_async_store

ConnectPolicy.default()    # 既定。指数バックオフで deadline=60s まで粘る
ConnectPolicy.fail_fast()  # リトライせず短い timeout で 1 回だけ（到達性を素早く判定）
ConnectPolicy.forever()    # 依存サービスが起動するまで無期限に粘る

async with open_async_store("nats", nats_url=..., policy=ConnectPolicy.fail_fast()) as store:
    ...
```

> `max_retry=inf` にするなら `deadline` を有限にしないと止まらない（既定は `deadline=60`）。

## 安全パス（Safe ラッパ）

任意の Store を 1 枚で包み、`validate_safe_path` で key/filename を検証してから委譲する。`open_async_store` は
これを内蔵する（生 backend を直接触らせない）。

```python
from manystore import SafeStore, UnsafePathError

safe = SafeStore(store)
await safe.put("a/b.txt", b"...")     # OK
await safe.put("../escape", b"...")   # UnsafePathError
```

## ストレージ UI / サーバ（`manystore[server]`）

任意の context（公開ディレクトリ/ストア）を **HTTP + WebSocket で公開する汎用ストレージ UI**。
ブラウザからキー閲覧・編集（フル CRUD）でき、ディレクトリ変更を WebSocket でライブ反映する。
重い依存（fastapi / uvicorn / watchdog）は optional extra なので、UI が要るときだけ入れる。

```bash
# 開発用ワンコマンド起動（既定ストレージ .cache/manystore_dev は使い捨て・自動作成）
make ui                    # = uv run python -m manystore.serving.server --config examples/manystore-ui.dev.toml
make ui PORT=9000          # ポート変更

# 自分の設定で起動する場合（配布利用時）
pip install "manystore[server]"
python -m manystore.serving.server --config examples/manystore-ui.toml   # 既定 http://127.0.0.1:8000
```

ブラウザで `http://127.0.0.1:8000` を開く。`make ui` は開発用設定（`examples/manystore-ui.dev.toml`）で
既定ストレージを `.cache/manystore_dev`（`.gitignore` 済みの使い捨て）にし、起動時に自動作成する。

設定（TOML）は context マウントと「ビューの重点設定」を宣言する:

```toml
default_context = "work"

[contexts.work]            # 公開する context（local は root、s3/nats は接続引数）
backend = "local"
root = ".work"

[[views.featured]]         # UI が標準で重点表示するパス（pin / その場で新規作成）
context = "work"
path = "skills/flow-memory-bank/interrupt"
label = "Interrupt"
pin = true
quick_write = true
```

UI 本体は特定用途（interrupt 等）を知らず、config が「重点パス」を pin/quick_write するだけ＝
**汎用 UI のまま**任意のパスを手早く扱える。protocol（REST/WS）は Store と 1:1 で対応し、
`manystore.client.RemoteStore` で 1 context をサーバ越しの Store として扱える。

- 構成: `manystore.serving.services`（backend 非依存の中核）/ `manystore.serving.server`（FastAPI）/ `manystore.client`（SDK）。
- 既定 bind は `127.0.0.1`。外部公開は `--host 0.0.0.0` を明示（フル CRUD を晒すため既定は自ホスト）。
- 監視は MVP では polling（全 backend 対応）。inotify ベースは後続の最適化。

## その他の公開 API

- `AsyncToSyncStore` — async ストアを同期 IF（`SyncStore`）として被せるゼロ依存ブリッジ。
- `ArrayStore` — 論理名（キー先頭セグメント）で複数 backend を束ねる合成ストア。`DownloadCache` 付き。
- backend クラス直指定: `LocalStore` / `S3Store` / `NatsStore` / `HttpStore` / `DictStore`（`Http*` は read-only）。
  いずれも値 API と IO API を持つ 1 つの Store（旧「KeyValueStore / FileStore」の 2 抽象は M071 で 1 Store に統合）。

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

## ライセンス

[MIT](LICENSE) © sasano8
