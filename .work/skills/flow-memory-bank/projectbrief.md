# Project Brief: manystore

## 概要 / スコープ

**manystore** は **ストレージ抽象ライブラリ**。`local` / `nats` / `s3` の各バックエンドを共通の
インターフェース（`KeyValueStore` / `FileStore`）の背後に隠し、利用側は backend を差し替えるだけで
保存先を切り替えられる独立ライブラリ。

**スコープ:** ストレージ抽象とその backend 実装・接続・安全パス・合成ストアまで。これを超える利用側
固有の要求は manystore 本体に取り込まず、利用側の adapter で吸収する（下記コア要件参照）。

## コア要件

- 共通 IF（`KeyValueStore` / `FileStore`）で local / nats / s3 を差し替え可能にする。
- async を一次実装とし、sync ブリッジを提供する。
- 接続ライフサイクル（connect / retry / timeout / deadline）と安全パス検証を備える。
- **最小・汎用に保つ**：利用側都合で IF を安易に拡張しない（最小プリミティブから合成。YAGNI）。

## ゴール

- 利用側が backend を adapter 経由で差し替えて使えること（結線は利用側に閉じる）。
- 単体で `uv run pytest` が緑（独立ライブラリとして自己完結）。
- 実 backend（SeaweedFS / 実 NATS）での疎通まで検証できること。
