# M071 完了設計：公開を 1 Store へ統合（a/b/c）＋ M073（contract/impl 分離・d）

ステップ1〜3 完了済（基底改名／基底に双方向合成＝full Store／factory 一本化）。本書は残り
**(a) クラス対の畳み込み・(b) BackendSpec 単一 factory・(c) facade 統合＋公開型の一本化・(d) M073
contract/impl 分離** の目標像と段階移行を定める。**非破壊を旧名 alias で担保**し、各段階で `make check`＋
`make test-heavy` 緑を確認しながら進める。

## 目標像（To-Be）

- **公開ストアは 1 つ**＝put/get（buffered API）＋ open_reader/open_writer（stream API）を持つ full Store。
  型は `AsyncStore`（現 `AsyncStreamingStore` を昇格・改名）。`AsyncBufferedStore` は「put/get だけ見たい」
  ための **view 型（別名）** として残す（撤去はしない＝型の narrowing に使える）。

### 内部基底タクソノミー（native-capability を型で表す・2026-07-02 追加）

「どの軸が native か（他軸は基底が合成）」を基底で表す。**公開型は 1 つ（`AsyncStore`）**で、これは内部の
実装区分:

```
_StoreBase                              … 共通表面（get/list_all/cp/mv/head は primitive から導出）
├─ BufferedStoreBase                    … put/get_or_raise=native（abstract）／open_*=合成
├─ StreamingStoreBase                   … open_*=native（abstract）／put/get=合成
└─ StreamableBufferedStoreBase          … put/get も open_* も native（4 primitive すべて abstract・合成なし）
```

- `StreamableBufferedStoreBase(_StoreBase)` は `_StoreBase`（put/get_or_raise 等 abstract）に
  `open_reader`/`open_writer` の abstract を足しただけ＝**両軸 native を強制**（合成にフォールバックしない）。
  多重継承は使わない（Buffered/Streaming の合成が衝突するため）。
- **`S3Store` はこれを継承**し、put/get（put_object/get_object）と open_*（multipart/range）を native 実装。
- conformance はこのタグで「native streaming を持つ backend」を判別でき、native IO 契約（M066③）の対象
  選定に使える。
- **backend は 1 クラス**（native がどちら寄りでも full Store）。native override があるものだけ実装を持つ。
- **registry は単一 factory**（`BackendSpec(name, factory, origin)`）。
- **アダプタ `KeyValueFileStore`/`KeyValueFromFileStore` は撤去**（合成は基底が持つ＝存在意義が消えた。
  当面 deprecated alias で残す）。
- **spec（契約）と impl（既定実装）を分離**（M073）。契約カタログは conformance と同居。

## (a) backend クラス対の畳み込み

各 backend を **1 クラス**へ。native override の有無で扱いが分かれる:

| backend | 現状（対） | 統合後の 1 クラス | native override |
|---------|-----------|------------------|-----------------|
| memory | `DictKeyValueStore` + `DictFileStore`（合成のみ） | `DictStore` | なし（基底合成） |
| local | `LocalFileStore` + `LocalKeyValueStore`(=FromFile 包み) | `LocalStore` | **IO が native**（get/put は基底合成） |
| s3 | `S3KeyValueStore` + `S3FileStore`（native multipart） | `S3Store`（`StreamableBufferedStoreBase` 継承） | **get/put も IO も native**（multipart/range＋part_size） |
| nats | `NatsObjectKeyValueStore` + `NatsFileStore`（合成のみ） | `NatsStore` | get/put native・IO は基底合成 |
| http | `HttpKeyValueStore` + `HttpFileStore`（read-only writer） | `HttpStore` | get native・open_writer=`UnsupportedOperation`（read-only） |
| manystore | `RemoteKeyValueStore`（既に単一） | `RemoteStore` | get/put native・IO は基底合成 |

- **旧クラス名は alias で存置**（`S3KeyValueStore = S3FileStore = S3Store` 等・非推奨）。外部が
  `isinstance`/import している可能性に配慮。
- `S3Store` は現 `S3FileStore`（S3KeyValueStore を継承し native IO を足したもの）を正とし、それを `S3Store`
  に改名して S3KeyValueStore/S3FileStore を alias 化。local も `LocalFileStore`→`LocalStore`、
  `LocalKeyValueStore` は alias。
- アダプタ利用箇所（tests/conformance の `KeyValueFileStore(...)` wrap）は「もう full なので wrap 不要」に
  置換（`_build_filestore` は `spec.factory()` を返すだけに）。

## (b) BackendSpec 単一 factory

- `BackendSpec(name, factory, origin)`（`kv_factory`/`file_factory` を 1 本の `factory` へ）。
- `register_builtin_backend(name, factory=...)` / `register_backend(name, factory=...)`。**旧 kwargs
  `kv_factory=`/`file_factory=` は当面受理**（`file_factory` は無視 or 検証）して alias 互換。
- `create_unsafe_store(backend)` = `spec.factory(**opts)`。deprecated な `create_unsafe_{kv,file}_store`
  は `create_unsafe_store` に委譲（file 版は「read-only/IO 非対応」時のみ従来 ValueError を模す必要が
  あれば維持、無ければ単純委譲）。
- entry-point plugin の契約（`docs/backend_registry.md`）も単一 factory へ更新。

## (c) facade 統合＋公開型の一本化

- **公開型**: `AsyncStreamingStore`→**`AsyncStore`** に改名（`AsyncStreamingStore` は alias）。
  `AsyncBufferedStore` は **view 型（alias）** として残す（put/get だけの型注釈用）。sync も
  `SyncStreamingStore`→`SyncStore`。
- **facade**: `manystore.store` を新設（統合入口＝`open_store`/`open_async_store`/`create_*_store`/
  `AsyncStore`/`SafeStore` 等）。`manystore.kv`/`manystore.file` は **deprecated alias**（当面 re-export）。
  トップ `manystore` は従来どおり全部フラット公開。
- **独立公開 `KeyValueStore` の扱い**: 撤去はせず **view 型として残す**（put/get だけ扱いたい利用者の
  ため）。ただしドキュメントは「既定は 1 つの Store」に寄せる。

## (d) M073：contract（spec）と impl の分離

`protocols.py` が「契約＋既定実装」を両持ちしている（drift 源・肥大）。分離する:

- **`manystore/spec/`（新）＝仕様の単一源泉**: 純粋な契約＝Protocol（`AsyncStore`/`AsyncBufferedStore`
  view/FileObject/sync 群）＋ 型（`FileInfo`/`IfMatch`/`Verify`）＋ conformance の**挙動契約カタログ**
  （`ContractSpec` 一覧・`assert_*` 群を `tools/conformancer` から移設 or 参照）。ここから spec 文書生成。
- **runtime impl は別モジュール**（`_StoreBase`/`BufferedStoreBase`/`StreamingStoreBase`・IO オブジェクト・
  `_kv_copy`/`_atomic_write_bytes`/`_sha256_hex`）＝backend が import する側。**test 機構は import しない**
  （層を保つ）。
- **検証ハーネス**（`FileStoreTester`・fault-injection）は test-time のまま（spec には入れない）。
- `protocols.py` は当面 **後方互換の re-export シム**として残す（既存 import を壊さない）。

## 段階移行（順序・各段で緑確認）

1. **(a) backend 1 クラス化**（native override を保持しつつ対を畳む・旧名 alias）。conformance 緑。
2. **(b) BackendSpec 単一 factory**（registry seed / create_unsafe_store を factory へ・旧 kwargs alias）。
3. **アダプタ撤去**（`KeyValueFileStore`/`KeyValueFromFileStore` を alias 化・利用箇所を full 直接に）。
4. **(c) 公開型改名＋`manystore.store` facade**（`AsyncStore`/`SyncStore`/`SafeStore`・kv/file を alias）。
5. **(d) M073 spec/impl 分離**（`manystore/spec/` 新設・`protocols.py` は re-export シム）。
6. docs（architecture / backend_registry / implementing_a_backend / url_scheme）を 1 Store 前提へ更新。

各段は **旧名 alias で非破壊**。最終的に alias 群は major バンプで撤去（別タスク）。

## 確定事項（2026-07-02・ユーザー合意）

1. **backend 1 クラス命名＝`Store` 統一**: `S3Store`/`LocalStore`/`DictStore`/`NatsStore`/`HttpStore`/
   `RemoteStore`。旧名（`S3KeyValueStore`/`S3FileStore` 等）は非推奨 alias で存置。
   **両軸 native の基底＝`StreamableBufferedStoreBase`**（S3Store が継承・4 primitive すべて native）。
2. **公開型＝`AsyncStore` に昇格**（`AsyncStreamingStore` は alias）。`AsyncBufferedStore` は put/get だけの
   **view 型 alias** で残す。sync も `SyncStore`（`SyncStreamingStore` は alias）。
3. **facade＝`manystore.store` 新設**、`manystore.kv`/`file` は deprecated alias（当面 re-export）。トップ
   `manystore` は従来どおり全フラット公開。
4. **M073＝`manystore/spec/` 新設で本ミルストンで実施**（契約＋挙動契約カタログを集約・`protocols.py` は
   re-export シム）。
5. **alias は当面存置**（本ミルストンは非破壊）。撤去は将来の major に回す。
