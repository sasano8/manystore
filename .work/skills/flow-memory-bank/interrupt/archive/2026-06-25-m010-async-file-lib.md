# M010 local backend 非ブロッキング化 — asyncio ベースのファイル操作ライブラリ導入

ユーザー要望（2026-06-25・対話）:

> M010 local backend の非ブロッキング化 — read_bytes/write を asyncio.to_thread でオフロード。
> asyncio ベースのファイル操作ライブラリを導入したいな。

## 背景

`manystore/storage/backends/local.py` の IO は全て同期で event loop を塞ぐ:
- `LocalFileObject.read/write/close`・`_LocalAtomicWriter.write/close/_abort`
- `open_reader`/`open_writer`（`.open()` がブロッキング）
- `iter_all`（rglob/stat）・`exists`/`delete`/`vacuum`/`mv`

## 技術論点（着手前に方針確定が必要）

「asyncio ベースのファイル操作ライブラリ」の実体は大きく2系統:
- **スレッドプール系**（`aiofiles` / `anyio.Path`・`anyio.to_thread`）= 内部は `run_in_executor`。
  `asyncio.to_thread` と機能的に同じ（真の async disk IO ではない）。**anyio は既に transitive 依存で在中**。
- **真の async IO 系**（`aiofile`＝caio/libaio）= Linux で本物の非同期 disk IO。C 拡張・Linux 偏重。

→ どれを採るかはユーザー判断（依存追加とトレードオフ）。AskUserQuestion で確定してから実装。
