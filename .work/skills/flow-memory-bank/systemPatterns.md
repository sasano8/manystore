# System Patterns

## システム構成

`manystore/` パッケージ（wheel packages = `["manystore"]`）。**個別ファイル名はここに列挙しない**
（移動で drift するため）。**公開 API の正本は facade `manystore.kv` / `manystore.file`**（＋トップ
`manystore` 再エクスポート）、**設計原則の正本は repo `docs/architecture.md`**（FileStore=KVS+IO・核の
配置/寄り・get_or_raise・conformance）。レイヤは概念で押さえる:

- **`protocols.py` = 契約＋既定実装の唯一の源泉**（2026-06-25 確定）。async/sync の Protocol（契約）に加え、
  backend が継承・流用する**既定実装**を 1 ファイルに集約: 基底 `FileStoreBase`（file 寄り）/
  `KeyValueStoreBase`（kv 寄り＝`get` を `get_or_raise` から与える）、2 方向アダプタ `KeyValueFileStore`
  （KVS→FileStore）/`KeyValueFromFileStore`（FileStore→KVS）、prefix capability ディスパッチ
  `iter_prefix`/`scan_prefix`、共有 IO オブジェクトと `_kv_copy`/`_kv_move`/`_atomic_write_bytes`。
  **`FileStore(KeyValueStore, Protocol)` = KVS + open_reader/open_writer**（原則7）。
3 バケットに分かれる（ディレクトリ＝役割なので個別ファイル名は追わない）:

- **`manystore/storage/` ＝ ストレージ・ライブラリ本体**。`backends/`（具象 backend）/ `surfaces/`（コアへの
  ラッパ・合成＝`Safe*`・`ArrayKeyValueStore`・`AsyncToSyncKeyValueStore`）/ facade `kv.py`・`file.py`。backend は
  `create_key_value_store`/`create_file_store`（"memory"/"local"/"s3"/"nats"/"http"）。http は read-only
  （write/list は `io.UnsupportedOperation`）、memory は依存ゼロ・揮発の参照 backend。
- **`manystore/serving/`（`manystore[server]` extra）＝ コアを HTTP で公開する層**。`services/`（旧 implement＝
  backend 非依存の中核：`StorageService`・protocol 契約 dataclass・config・`PollingWatcher`・s3map）/ `server/`
  （FastAPI native REST/WS＋同梱 Web UI）/ `gateway/`（S3 互換）。`combined.py`（top）が両者を単一 lifespan で束ねる。
- **`manystore/tools/conformancer/` ＝ 適合性ツール**（Protocol メソッド存在チェック＋FileStoreTester）。
- **トップ直下** — `protocols.py`（契約＋既定実装の源泉・上記）/ `connect.py`（`connect_key_value_store`/`connecting`/
  `ConnectPolicy`）/ `exceptions.py` / `client/`（`ManystoreClient`・`RemoteKeyValueStore`）。
- **安全な入口（顔）** — `open_async_key_value_store` / `open_async_file_store`（Safe 包装必須の接続 CM）。

## 主要な技術判断

- **公開は 2 名前空間にグルーピング**：`manystore.kv`（値ストア群）/ `manystore.file`（ファイル群）に
  facade を分け、トップ `manystore` は後方互換で両者をフラット再エクスポート（`__all__` は両 facade の
  `__all__` を dict.fromkeys で重複畳み込み）。ruff の re-export 検出のためトップは star import + `# noqa: F403`。
- **FileStore はバイナリ専用の方向別 API**：`open(mode)` を廃止し `open_reader(filename)` / `open_writer(filename)`
  に置換（方向が型に出てテスト容易・テキスト符号化は利用側責務）。全 *FileStore・KeyValueFileStore・
  SafeFileStore・SyncFileStore Protocol を更新。HttpFileStore は read-only ＝ `open_writer` は `io.UnsupportedOperation`。
- **2 ストア抽象は包含関係**：`FileStore = KeyValueStore + {open_reader, open_writer}`（`FileStore(KeyValueStore,
  Protocol)`）。KVS は FileStore から IO を除いた部分集合。`get` の primitive は `get_or_raise`（欠損は
  `FileNotFoundError` に正規化）で、`get(key, default=None)` は基底 `KeyValueStoreBase` が捕捉して与える
  （各 backend は get_or_raise だけ実装）。backend = `Local` / `S3` / `Nats` / `Http`。Local は init で絶対パス固定
  （cd 非依存）、put は親ディレクトリ作成、**list_all は全キーを平坦に再帰列挙**（rglob、相対 posix キー・'/' ネストも。
  1 階層概念は持たず KVS はフラット。limit は安全上限）で s3/nats のフラットキー規約に整合。Local の同期 IO は
  全て `anyio.to_thread.run_sync`（`_offload`）でスレッドへ逃がし event loop を塞がない（M010・スレッドプール系）。
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
6. **バッファ性は IF の本質で決まる（2026-06-23・ユーザー方針）** — **KV は本質的にバッファ概念**（全体 get/put）、
   **FileStore/stream はバッファ無し概念**（open→逐次 IO）。どちら向きに adapter で被せても、KV が現れる所に
   バッファが現れるのは当然で許容する。**真にバッファされないのは「バッファ無しストレージをそのまま露出した場合」
   だけでよい**（KVS→FileStore の `KeyValueFileStore`、FileStore→KVS の `KeyValueFromFileStore` はどちらも KV 層で
   バッファ＝「みせかけのストリーム」になる）。本プロダクトの目的は **IF の整理**であり、サーバとして提供する限り
   バッファ層を隠蔽したストレージが真のストリーム性を出せないのは仕方ない。**真の性能はクライアント側でラップして
   得る＝真髄はクライアントプログラムにある**（サーバ越しに無理にストリームを通さない）。これは M026 stream IF の
   設計指針にも効く（HTTP 公開＝buffered 前提、真の streaming は client wrap）。

7. **核（真実の実装）は native primitive 側に置く** — 設計原則の**正本はリポジトリの `docs/architecture.md`**
   （Memory Bank は一時記憶なのでここには要約のみ／正式原則を置かない）。要約: backend ごとに kv 寄り/file 寄りを
   見極め、逆派生で性能が落ちる方を核に。kv 寄り＝`XFileStore(XKeyValueStore)`（S3=native streaming / NATS・HTTP・dict
   =buffer 合成）、file 寄り＝`KeyValueFromFileStore(XFileStore)`（Local）。`FileStore = KeyValueStore + IO`。
   準拠は `manystore.tools.conformancer`（メソッド存在チェック）で横断確認。read-only（HTTP）は write 系が
   `io.UnsupportedOperation`。**詳細・backend 別表・conformance の使い方は `docs/architecture.md` を見ること。**

## コンポーネント関係 / 重要な実装経路

- **推奨入口（ライブラリの顔）= `open_async_key_value_store` / `open_async_file_store`**（M032）＝Safe 包装必須の
  接続 CM（`async with` で connect＋`Safe*` 包装、終了で aclose）。低レベルは `create_*`（生・未接続）/
  `connect_key_value_store`（接続のみ・Safe 無し）。`create_file_store` は FileStore 版ファクトリ。
- 危険入力対策は `Safe*` ラッパが `validate_safe_path` で key/filename を検証してから委譲。`SafeFileStore` は
  `SafeKeyValueStore` を継承＝KVS 面（検証付き）＋ IO（open_reader/open_writer）の完全な FileStore。
- 複数 backend の横断は `ArrayKeyValueStore`（キー先頭セグメント＝論理名で振り分け）。`mount`/`unmount` は
  **登録のみ（同期・I/O なし）**で connect/aclose しない（M011-②）。接続ライフサイクルは顔
  `open_async_array_store(mounts)`（`SafeKeyValueStore(ArrayKeyValueStore)` 包装＝全 mount を connect/aclose する CM）が
  一括で担う＝mount の登録と接続の二重責務を分離。`StorageService` は明示 connect + 同期 mount。
