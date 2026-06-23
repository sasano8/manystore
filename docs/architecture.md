# manystore アーキテクチャと設計原則（正本）

> このファイルが設計原則の **正本**。実行可能な正本は Protocol 定義（`manystore/async_storage.py`）と
> 準拠テスト（`manystore/conformance.py` / `tests/test_conformance.py`）。

## 2 つのストア抽象と包含関係

```
KeyValueStore : put / get / get_or_raise / iter / list / exists / delete / cp / mv / connect / aclose
FileStore     : KeyValueStore を継承 ＋ open_reader / open_writer（ストリーム IO）
```

`FileStore = KeyValueStore + {open_reader, open_writer}`（`class FileStore(KeyValueStore, Protocol)`）。
KeyValueStore は FileStore から IO を除いた**部分集合**。値はすべてバイナリ（`bytes`）。

## get のセマンティクス

- primitive は **`get_or_raise(key) -> bytes`**：キーが無ければ `FileNotFoundError`（コードベース全体で欠損の正規例外）。
- **`get(key, default=None) -> bytes | None`** は基底 `KeyValueStoreBase` が `get_or_raise` を捕捉して与える既定実装。
  backend は **`get_or_raise` だけ実装**すればよい（try/except を各所で重複させない）。

## 原則: 核（真実の実装）は native primitive 側に置く

backend ごとに **kv 寄り / file 寄り**を見極め、**逆向きに派生すると性能が落ちる方を核**にする（二重実装しない）。

- **kv 寄り（核 = KVS）= `XFileStore(XKeyValueStore)`** — native primitive が whole get/put。KVS に真実の実装を置き、
  FileStore は KVS を継承して **IO だけ足す**：
  - backend が真の streaming を持てば **native streaming**（例: S3 = range body / multipart で大オブジェクトを定メモリ）。
  - 持たなければ **whole の上に buffer 合成**（NATS / HTTP / dict、共有 `_KvReadFileObject` / `_KvWriteFileObject`）。
  - ※ whole を streaming から逆派生すると小さい値で multipart 過剰＝遅い。だから S3 でも核は KVS（whole）側。
- **file 寄り（核 = FileStore）= `XKeyValueStore = KeyValueFromFileStore(XFileStore)`** — native primitive が stream IO
  （open/read/write）。FileStore に真実の実装を置き、KVS は IO を隠した派生ビュー（例: Local）。

派生側（薄いビュー）に backend 固有ロジックを重複させない。片側固有操作（例 Local の `vacuum`）だけ薄いビューに足す。

### backend 別の核配置

| backend | 寄り | 核 | IO の出自 |
|---|---|---|---|
| Local | file | `LocalFileStore` | native（filesystem の open/read/write）。KVS = `KeyValueFromFileStore(LocalFileStore)` |
| S3 | file | `S3KeyValueStore`（whole）＋ `S3FileStore` で IO | native streaming（range body / multipart） |
| NATS | kv | `NatsObjectKeyValueStore` | buffer 合成（真の streaming は nats-py 仕様で deferred） |
| HTTP | kv（read-only） | `HttpKeyValueStore` | buffer 合成（GET=whole）。write 系は `io.UnsupportedOperation` |
| dict（memory） | kv | `DictKeyValueStore` | buffer 合成 |

## 2 方向の汎用アダプタ

- `KeyValueFileStore`（KVS→FileStore）= **IO の埋め合わせ**：open_reader/open_writer を合成し、KVS 面は下層へ委譲。
- `KeyValueFromFileStore`（FileStore→KVS）= IO を落とすだけ（残りは下層へ委譲）。

## read-only の表現（既知の制約）

Protocol は read-only を静的に表せない。read-only backend（HTTP）は write 系メソッドが**型上は存在するが呼ぶと
`io.UnsupportedOperation`** を投げる（現状の方針＝YAGNI。capability 分割は需要が出てから別途）。

## 準拠の確認（conformance）

サードパーティ backend は `manystore.conformance` で横断的に検査できる：

```python
from manystore.conformance import assert_key_value_store, assert_file_store
assert_key_value_store(MyKeyValueStore(...))   # KVS のメソッドが揃うか
assert_file_store(MyFileStore(...))            # FileStore（= KVS + IO）が揃うか
```

**現状はメソッド存在チェックのみ**（`typing.get_protocol_members` が返す Protocol メンバが callable な属性として
在るか）。シグネチャ・挙動の一致（契約スイート）は開発途上のため未実装＝必要になってから足す。
