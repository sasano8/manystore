---
from: user
type: request
priority: normal
date: 2026-06-26
---

## 要望
KeyValueStore.put の戻りを `None` から **共通レスポンス `FileInfo`** に変える。
「様々な KV ストアで共通のレスポンスを protocols.py に書きたい」。

## 確定した設計（ユーザー選択）
- `put -> FileInfo`（`{filename, size}`）。全 backend が round-trip なしに生成可
  （`return {"filename": key, "size": len(value)}`）。
- revision/etag は共通ではない＝今回は載せない（必要なら後日 `SupportsVersionedPut`
  capability として切り出す。fail-loud / 最小-core 方針を維持）。

## 後続判断（2026-06-26）
- 「backend 生レスを含む request/response 封筒も返したい」案が出たが**却下**。
  理由: file/value 抽象に transport の response を持ち込むのはパラダイム不一致（ユーザー判断）。
  → `put -> FileInfo` は維持。封筒（StoreResponse / put_response capability）は追加しない。
  詳細は progress.md「意思決定の変遷」参照。

## lockstep 波及（M043 parity 上、全部揃える必要あり）
- AsyncKeyValueStore.put ↔ SyncKeyValueStore.put（Protocol async/sync）
- FileStoreBase.put（IO 導出の既定実装）
- KeyValueFileStore.put / KeyValueFromFileStore.put（委譲アダプタ）
- _KvWriteFileObject.close（put 呼び出し・戻りは破棄でよい）/ _kv_copy
- conformancer（M043）の parity assert が緑であること
