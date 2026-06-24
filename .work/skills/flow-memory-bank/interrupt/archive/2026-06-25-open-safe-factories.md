# 要望（対話・2026-06-25・ユーザー）— M032 具体化

Safe 包装込みのファクトリを作る。

- `open_async_key_value_store` / `open_async_file_store` を新設する。
- これらは**ライブラリの顔**となる場所（トップ `manystore`）で公開する。
- **safe を必須**にする（opt-out で生を取る口は設けない＝包装が外せない）。

## 文脈
- 現状 `create_key_value_store(backend, ...)` は生ストアを返す（Safe 包装は利用側任せ＝foot-gun）。
- FileStore 用の生成口（`create_file_store`）は未整備。
- `connect_key_value_store` は接続 CM だが Safe 包装はしない。

## 要設計（着手前に確定）
- `open_*` は「接続 CM（async with で connect＋Safe）」か「構築のみ（Safe 包装した未接続ストア）」か。
- FileStore 版の backend→FileStore マッピング（Dict/Local/S3/Nats/Http）。
- `connect_key_value_store` / `create_key_value_store` との棲み分け（顔をどれにするか）。
