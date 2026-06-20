# System Patterns

## システム構成

`manystore/` パッケージ（wheel packages = `["manystore"]`）。主なモジュール：

- `async_storage.py` — 一次実装。抽象（`KeyValueStore` / `FileStore` Protocol）＋共通ヘルパ
  （`_take` / `_atomic_write_bytes` / `_kv_copy` / `_kv_move`）＋汎用アダプタ `KeyValueFileStore`。
- `sync_storage.py` — 同期 IF（`SyncKeyValueStore` / `SyncFileStore` / `SyncFileObject`）。
- `async_to_sync_storage.py` — `AsyncToSyncKeyValueStore`（専属ループを `run_until_complete` で駆動する
  ゼロ依存ブリッジ）。
- `backends/` — `local.py` / `s3.py` / `nats.py` / `http_store.py` ＋ `__init__` の `create_key_value_store`。
  `http_store.py` は **read-only**（GET/HEAD のみ。書き込み・一覧は `io.UnsupportedOperation`）。ファイル名は
  stdlib `http` パッケージと紛れないよう `http_store`（backend 識別子は `"http"`）。
- `connect.py` — `connect_key_value_store` / `connecting` / `ConnectPolicy`。
- `safe_path.py` — `validate_safe_path` ＋ `SafeKeyValueStore`（download/キャッシュも担う唯一の KVS wrapper）/
  `SafeFileStore`。
- `array_storage.py` — `ArrayKeyValueStore`（論理名＝マウント先で複数 backend を束ねる汎用ストア）
  ＋ `DownloadCache`。

## 主要な技術判断

- **2 ストア抽象**：`KeyValueStore`（put/get/list/exists/delete/cp/mv）と `FileStore`（`open`→`FileObject`）。
  backend = `Local` / `S3` / `Nats…`。Local は init で絶対パス固定（cd 非依存）、put は親ディレクトリ作成、
  list は再帰（rglob、相対 posix キー）で s3/nats のフラットキー規約に整合。
- **接続ライフサイクル**：init では接続せず `async with`（`connecting`）で接続。`verify` は接続確認の
  ON/OFF、`ConnectPolicy` は retry/timeout/deadline/backoff（プリセット `default()`/`fail_fast()`/`forever()`）。
  1 回の待機は timeout と残り deadline の小さい方で縛る。
- **アトミック書き込み**：local は temp+`os.replace`。s3（multipart complete）/nats（put 完了まで不可視）は
  元々アトミックなので追加対応なし。
- **NATS の注意点（過去のバグ）**：`ObjectStore` に `info` は無く正は `get_info`。`get(writeinto=...)` は
  別スレッドで書くため `asyncio.Queue` は使えず、バッファ読み（`obs.get(name).data`）にしてある。

## 設計パターン / 原則

1. **最小・汎用に保つ** — 利用側都合で IF を拡張しない。拡張が要るなら doc-first で合意（YAGNI）。
2. **ラッパは 1 枚・差し替えるのは backend だけ** — ネスト禁止。
3. **抽象 IF を backend 固有事情で汚さない** — 例: `vacuum`（空ディレクトリ削除）は Local 固有なので
   Protocol に載せない（s3/nats はフラットで概念なし）。
4. **アトミック書き込み（all-or-nothing）**。
5. **YAGNI** — 必要になるまで実装しない。

## コンポーネント関係 / 重要な実装経路

- `connect_key_value_store(backend, ...)` が入口 → `backends.create_key_value_store` → backend 具象。
- 危険入力対策は `Safe*` ラッパが `validate_safe_path` で key/filename を検証してから委譲。
- 複数 backend の横断は `ArrayKeyValueStore`（キー先頭セグメント＝論理名で振り分け）。
