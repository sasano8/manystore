# 要望（対話・2026-06-24・ユーザー確定）

M035 後続。プロトコルの配置と継承方向を整える。

## 1. protocols.py 集約
`sync_storage.py` を消し、async/sync の Protocol を `manystore/protocols.py` に集約する
（`FileInfo`/`KeyValueStore`/`FileStore`/`FileObject`/`SupportsPrefixListing` ＋ `SyncKeyValueStore`/
`SyncFileStore`/`SyncFileObject`）。stores/base.py は実装だけ（Protocol を protocols.py から import）。
→「async 版プロトコルがどこか一目で分かる」「sync↔async 突合がしやすい」。

## 2. sync↔async インターフェースのズレを合わせる（3 点）
- **teardown**: async `aclose()` に対し `SyncKeyValueStore` Protocol が teardown 未宣言（bridge 実装は
  `close()` を持つ）→ Protocol に `close()` を宣言（async aclose ↔ sync close の a-prefix 対応）。
- **`SyncFileStore(SyncKeyValueStore, Protocol)`**: KVS を継承して「FileStore = KVS + IO」を sync にも反映。
- **sync prefix capability**: async の `SupportsPrefixListing` 相当を sync にも（optional・対称性のため）。

## 3. FileStoreBase 新設＋継承方向の是正
- `class LocalFileStore(KeyValueStoreBase)` は逆＝Local は **file 寄り**で primitive は open_reader/open_writer
  （現に get_or_raise は open_reader 経由）。
- **新 `FileStoreBase`**: primitive=open_reader/open_writer（abstract）、get_or_raise/put/get を IO から
  **導出**（値境界で whole バッファ）。`KeyValueStoreBase` は継承しない（file 寄りは KV を継承しない）。
- `LocalFileStore(FileStoreBase)` に付け替え＝IO ＋名前空間操作(iter/exists/delete/cp/mv/vacuum)だけ実装。
- **kv 寄り backend（NATS/dict/HTTP）は `KeyValueStoreBase` のまま**（バッファが元から生じる＝KV を元に実装）。

## 設計判断（確定）
- **Protocol** は契約: `FileStore(KeyValueStore)` / `SyncFileStore(SyncKeyValueStore)` は KV を継承したまま
  （FileStore ⊇ KVS）。
- **Base 実装クラス** は primitive 方向: file 寄り=`FileStoreBase`／kv 寄り=`KeyValueStoreBase`。
- 公開 API（facade kv/file の名前）は不変＝behavior-preserving。
