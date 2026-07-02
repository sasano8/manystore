# Progress

> 完了マイルストーンは**要点1行**に畳む（実装の経緯は git 履歴）。残作業は着手できる粒度で保持する。
> 設計原則の正本は repo `docs/architecture.md`、公開 API の正本は facade `manystore.kv`/`manystore.file`。

## 動くもの（What works）

- **2 ストア抽象**（`KeyValueStore` / `FileStore`＝KVS + open_reader/open_writer）と backend
  （local / s3 / nats / http〔read-only〕 / dict(memory)〔依存ゼロ・揮発の参照 backend〕）。S3/NATS/HTTP/dict すべて
  完全な FileStore（KVS+IO）に準拠。核は native primitive 側（S3=file 寄り native streaming／NATS・HTTP・dict=kv 寄り
  buffer 合成／Local=file 寄りで `LocalKeyValueStore = KeyValueFromFileStore(LocalFileStore)`）。
- **get の duality**: primitive `get_or_raise`（欠損→`FileNotFoundError`）＋基底 `KeyValueStoreBase` が `get(default)` を
  供給。`get_or_raise` は `@abstractmethod`＝実装漏れはインスタンス化時 `TypeError`。client/service 層も具備。
- **prefix 列挙は core 引数**（`iter_all(prefix="")`/`list_all(prefix="")`）＝S3 はサーバ側 `Prefix=` で native／
  他（local/dict/nats/remote）は scan+filter を契約上の既定動作で実装（旧 `SupportsPrefixListing` capability は
  2026-06-26 廃止＝意思決定の変遷参照）。
- **条件 put / CAS**（M046）: `put(key, value, *, if_match=None)`＝None=LWW／`FileInfo.absent()`=create-only／
  `FileInfo`=update CAS（不一致 `ConflictError`）。比較トークンは `FileInfo.etag`（S3=ETag・local=mtime_ns+size・
  dict=世代・NATS=メタ subject 最終 seq）。`head(key)->FileInfo` が version 読み口。全 backend＋native REST＋remote＋
  S3 GW まで配線済。
- async / sync ブリッジ（`AsyncToSyncKeyValueStore`）、接続ライフサイクル（`connect_key_value_store`/`connecting`/
  `ConnectPolicy`）、安全パス（`validate_safe_path`/`SafeKeyValueStore`/`SafeFileStore`）、合成（`ArrayKeyValueStore`/
  `DownloadCache`）、片方向同期 `StorageMirror`（`storage/sync/`）。
- **適合性ツール `manystore.tools.conformancer`**: メソッド存在チェック＋`FileStoreTester`（DictFileStore をオラクルに
  差分検証＝run_light/middle/heavy/full）＋**オラクル非依存の絶対契約**（writer all-or-nothing・CAS 並行・非CAS 並行・
  fail-loud〔in-process/transport〕＝`ABSOLUTE_CONTRACTS`）。契約カタログ→spec 文書／scaffold／drift ガード。
  `tests/conformance_providers.py` で全 provider を 1 宣言→ matrix が dict/local/remote/実 nats/s3 へ非破壊で流す。
- **UI/サーバ（`manystore[server]` extra）**: `serving.services`/`serving.server`/`client` の 3 層。任意 context を HTTP+WS で公開する
  汎用 CRUD UI。context = ArrayStorage 第一階層（bucket）。native REST は `{bucket}/{path}`、WS ライブ通知、
  `views.featured` で重点パス。`RemoteKeyValueStore` でサーバ越し KVS。
- **S3 互換ゲートウェイ `manystore.serving.gateway`**: GET/PUT/HEAD/DELETE/ListObjectsV2 + Multipart を `StorageService` 上に
  1:1 合成（コア IF 不変・実 aiobotocore 往復で検証済）。
- **統合エントリポイント `manystore.combined`（`python -m manystore`）**: native REST/WS（`/kv/raw`・buffered）と
  S3 ゲートウェイ（`/storage/s3`・streaming）を単一 lifespan で束ねる。
- **CI**: GitHub Actions（`ci.yml`）＝`check` ジョブ（`make check`＝fast）＋**`e2e` ジョブ（M061）**＝docker compose で
  nats/seaweedfs/minio を起こし `MANYSTORE_E2E_REQUIRED=1 make test-heavy`（実 backend・skip 不可）。**テスト軽重
  分離**＝`make test`（fast・`-m "not slow"`）/`make test-heavy`（slow＝実 backend）/`make test-all`（全部）。
- **Docs サイト（GitHub Pages・2026-06-25）**: `pages.yml` で MkDocs Material をビルド＝`make docs`（先に `make conformance-docs`
  で spec 再生成 → `mkdocs build --strict`）。PR はビルド検証のみ・**main push のみ公式 Actions（upload-pages-artifact/
  deploy-pages）で実公開**。`index.md` は snippets で README を取り込む単一ソース。docs 依存は `docs` group（mkdocs-material）。
  **要手動: Settings → Pages → Source = GitHub Actions を有効化**（初回のみ・ユーザー作業）。
  実 backend E2E（NATS / S3 path-style＝SeaweedFS・MinIO）検証済（`make e2e-up`→gated matrix・CI `e2e` ジョブで実走）。
- **local backend は非ブロッキング**（M010）: 全 IO syscall を anyio でスレッドへオフロード＝event loop を塞がない。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

> **方針（2026-06-28・ユーザー指示）＝品質優先・拡張後回し**。現プロダクトの品質を高める作業（下記
> 「品質強化」＝correctness バグ修正・lifecycle 健全化・テスト/カバレッジ強化・DRY/cleanup）を最優先で消化する。
> **拡張的タスク（新 surface/backend/IF）は後回し**＝`M051`(k8s)/`M039`(IPFS)/`M040`(LB)/`M026`(stream)/
> `M045`(put2)/`M028b`(動的 mount) は品質強化が一段落するまで着手しない（下表に残すが優先度は最下）。
> 品質強化タスク（M054〜M064）は 2026-06-28 の 4 観点コード監査（error handling / lifecycle・並行 / test
> coverage / DRY・API 一貫性）で抽出。high 指摘は実コードで裏取り済。

### 品質強化（最優先・2026-06-28 監査で抽出）

> **全項目 完了済み**（下記「完了マイルストーン」へ昇格）。次は「機能・完成度」へ。

> **完了済み（下記「完了マイルストーン」へ昇格）**: M054/M055（fail-loud）・M056（nats lock）・M057（lifecycle
> ロールバック）・M058（writer all-or-nothing）・M059（pytest-cov）・M060（crypto pytest）・**M061**（実 backend e2e
> を CI で gated 実走＝skip 許容やめ）・M062（list_all 集約）・M063（DownloadCache async）・M064（cp/mv identity
> 設計固定）・**M065**（conformance を仕様の単一源泉に＝北極星 ①〜④＋fail-loud〔in-process/transport〕＋
> 非CAS並行＋run_full）・**M066**（挙動契約の集約ハーネス）。
> M016（CAS 以外の並行/大容量/エラーパス）は並行系を M065 step8 で契約化済＝残は StorageMirror 実行中の source 変更。

> M016（既存・下表）も本監査で具体化＝CAS 以外の並行操作（並行 delete/get・Mirror 実行中の source 変更）／大容量・チャンク境界／backend エラーパスの fail-loud 検証が手薄。M059 で可視化 → M016 で穴埋め。

### 機能・完成度（品質強化の後）

| ID | タスク | 優先 | 備考 |
|----|--------|------|------|
| M051 | kubernetes backend（M050 の具体 sink） | 相談 | ユーザー要望（2026-06-27・討議中・doc-first）＝put=server-side apply / get=補完済み live（put≠get）。キー=`namespace/resource_type/name`（`.yml` 含めず・group/version は discovery 補完・衝突時 `type.group`）。**FileInfo に世代情報**（resourceVersion/generation/uid/creationTimestamp）。**resourceVersion CAS を M046 の参照実装**に（`if_version` 不一致は ConflictError）。ローカル側=`KubeManifestStore` ラッパ（パス↔内容の同一性検証＝Safe 風 1 枚）。依存=`kubernetes-asyncio` を extra `[k8s]`（遅延 import）。cluster-scoped は後回し。詳細は interrupt |
| M012 | `list(prefix=...)` / pagination | 中 | prefix は core `iter_all(prefix=…)` 引数化済（M030 capability は 2026-06-26 廃止）。**pagination 未対応**。設計案（2026-06-26 対話・要 doc-first）＝(a) `iter_all`/`list_all` に **offset+limit** を足す（単純・全 backend で scan 可だが大 offset は O(n)）／(b) **cursor/continuation-token** 形式（S3 ContinuationToken・NATS 等の native と整合・M021 の continuation と同一機構）。加えて **返り値を range メタ付きの独自型**にする案＝iter は「何件目〜何件目」を、list は from/to 件数属性を持つ（pagination メタ＝**file/value パラダイム内**。却下した transport の request/response 封筒とは別物）。未確定＝offset/limit vs cursor の二択と、独自結果型を入れるか。M021（S3 GW continuation）・M044（limit 既定の定数化）と連動 |
| M013残 | メタデータ / content-type | 中 | **sha256 メタ充填は完了**（2026-07-02＝M067 残B。put 時 `_sha256_hex` を native メタへ〔dict/s3=x-amz-meta/nats=ObjectInfo digest〕→head 露出→HTTP ヘッダ透過→array 透過。native メタ無しの local は None=best-effort。conformance `meta.sha256_correct` 追加）。**残＝content-type と汎用 user metadata dict**（first-class にするか汎用 dict か未確定・doc-first） |
| M016 | テスト拡充（エラーパス/並行/大容量） | 中 | fake は happy path 中心 |
| M014 | 操作レベル retry/timeout | 低 | 現状 connect のみ |
| M015 | logging（操作・リトライ可視化） | 低 | 観測性なし |
| M021残 | S3 ゲートウェイ 残 | normal | S1/S2（GET/PUT/HEAD/DELETE/ListObjectsV2 + Multipart）実装済。残＝S3 passthrough（`SupportsPresign`+redirect/proxy）/ S4 SeaweedFS 実機 backend 疎通 / ListObjectsV2 continuation token ページング。設計 `plans/m021-s3-gateway-plan.md` |
| M022b | conformance の run_middle/heavy/full ＋ spec（file/kv 寄り）検出・特性表・リプレイ | low | P1 存在チェック＋P2 run_light 完了。`tester.spec={"leaning":None}` は placeholder。実 backend（S3/NATS）適用も |
| M027b残 | FileStore=KVS+IO 波及（Sync 残） | low | S3/NATS/HTTP/Local＋`SafeFileStore` 完了（Safe は M032 で `SafeKeyValueStore` 継承＝KVS 面も検証付き委譲に）。残＝`SyncFileStore` Protocol 鏡映＋`AsyncToSyncFileStore` ブリッジのみ |
| M025残 | 名前空間再編 フェーズ2/3 | normal | フェーズ1（移設）＋addressing 再設計 完了。残＝フェーズ2 `kv/json`（JSON 検証）/ フェーズ3 `storage/manystore`（range/chunked streaming）。設計 `plans/m025-namespace-restructure-plan.md` |
| M026 | stream インターフェース（第3の族・新コア IF） | 相談 | kv/storage の他に **stream**＝無境界チャネル（append/follow＝tail/subscribe）。FileStore で表せない＝新コア IF `StreamStore`。MVP=byte stream。最小・汎用と緊張するので **doc-first 合意必須**（着手時に設計を起こす。旧 interrupt は GC 済＝git 履歴に残存）。**分割軸の確定（2026-06-26 対話）**＝stream を切る軸は「jsonl vs pub/sub」でなく **「送信単位ごとに応答チャネルがあるか」の1点**。(A) 片方向 fire-and-forget（`write->None`/`AsyncIterable`・エラー＝stream の死）＝**StreamStore＝コアに属す（FileStore IO 寄り・原則6）**／(B) 単位ごとに応答（`request->Response`＋相関ID・エラー＝per-unit ステータス＝N 個の request-response 多重化）＝**メッセージング/トランスポートでストレージ抽象でない→コア IF に載せず `client/` の別レイヤ（仮称 Exchange/RPC）として別途 doc-first**（YAGNI・projectbrief スコープ・原則6 の client wrap 方針）。中間 reliable one-way（配送 ack/no app 応答）は (A) の信頼性オプション。設計 `interrupt/archive/2026-06-26-stream-if-split-axis.md` |
| M028b | ArrayStorage を HTTP に動的公開（context の mount/unmount） | low | `POST/DELETE /contexts` で動的 mount。backend 資格情報を HTTP から渡す＝認証設計が要る（M011 連動）。**動的化の核**＝非同期 `attach`/`detach`（connect→登録 / 登録解除→aclose を `asyncio.Lock` で直列化。並行 POST/DELETE の競合・リーク防止）。`mount`/`unmount` の IF は既に非同期化済（中身は登録のみ）＝ロック実装を後付けできる。要設計 |
| M039 | IPFS backend 本実装 | 相談 | scaffold 配置済（`backends/ipfs.py`・本体 NotImplementedError・**factory 未接続**）。MFS（`/api/v0/files/*`）主＝パス鍵で KVS に乗せる／CID 直は従（フック `cid_add`/`cid_get` のみ）。httpx 流用。接続ネタ＝api_url/gateway_url/token/mfs_root/pin_on_write/timeout。本実装時に factory `"ipfs"` 分岐を足す |
| M041 | nats not-found catch を撤去 | low | `nats.iter_all`/`exists` の `NotFoundError` catch（空/欠損の正規化）を将来 `obs.watch()` ベース再実装で取っ払う。M036 の残置（コード内 `# TODO(M041)`）|
| M042 | transport 層の整理 | low | `client/remote.py` の Safepath Client / RemoteKVS の所属切り分け（コード内 `# TODO(M042)`）。設計musing を backlog 化 |
| M040 | ロードバランサーストレージ層 本実装 | 相談 | scaffold 配置済（`surfaces/loadbalancer.py`・本体 NotImplementedError・**facade 未公開**）。**負荷メトリクスで適切な1 backend を選ぶ**動的プレースメント（シャーディング/レプリケーションではない）。ネタ＝capability `SupportsLoadStats`/`LoadStats`＋`BalancePolicy`（RoundRobin/MostFreeSpace/LeastLoaded）。Array の兄弟。**未解決＝読みルーティング**（probe-all 既定 vs 配置インデックス）。local の free は `shutil.disk_usage`、cpu/mem は別途エージェント/エンドポイント要 |
| M045 | `put2` ＝ error-as-value（Go 風 `(Error\|None, FileInfo)`） | 相談 | 別メソッド `put2(key, value) -> tuple[Error \| None, FileInfo]`＝成功は `(None, FileInfo)`／失敗は `(Error(...), FileInfo?)` で **エラー側に任意情報を載せられる**（“**半分** request/response 型”＝成功は FileInfo のまま・封筒は被せない。却下した full envelope とは別物）。要 doc-first。**未確定**＝(1) 例外ベース fail-loud（既存 put が raise）との二重化＝どの op が raise／どの op が tuple か、混在の指針／(2) `Error` 型の定義（共通基底 or 既存例外 `Exception\|None`／backend 固有情報の持たせ方）／(3) core IF に載せるか別系統 method か（載せるなら async↔sync lockstep ＋ conformancer parity ＝M043 前提）／(4) get/delete 等への波及。put→FileInfo（済）と request/response 封筒却下（projectbrief 非ターゲット）の中間地点 |
| M076 | nats fake を conformance provider に（JetStream メタ subject 忠実化） | 中 | M074 の follow-up＝nats fake の conformance provider 化は保留した。理由＝nats backend の head/version/CAS は JetStream の**メタ subject**（`obs._js.get_last_msg(stream, meta_subject)`＝per-key の seq/size/deleted を JSON メッセージで持つ）に依存し、`FakeNatsObs` はこれを模していない（`_js` が無く head/CAS で AttributeError）。課題＝`FakeNatsObs` に最小の `_js.get_last_msg` 相当（put でメタ書き込み・delete で tombstone・get_last_msg で最新）を足し、nats-fake provider を非 gated で追加（CAS は非権威 xfail）。**嘘の温床にしない**＝観測契約のみ忠実に、並行/CAS は非権威。s3-fake（M074 で実装済）が先例 |
| M071 | 公開 IF 統合＝`KeyValueStore` 廃し 1 ストアへ（Buffering/Nobuffering 再編） | 進行中 | **ステップ3 完了（2026-07-02）＝factory/顔を 1 本化**（旧名は存置）: `create_unsafe_store`（=`file_factory or kv_factory`＝常に full Store・KVS-only の manystore も作れる）／`create_safe_store`（=`SafeStore` 包装）／`open_async_store`（Safe＋接続 CM）／`SafeStore`（=`SafeFileStore` 別名）を新設。`open_store(url/名前)` も full Store を返すよう統一。旧 `create_unsafe/safe/open_async_{key_value,file}_store`・`SafeKeyValueStore`/`SafeFileStore` は非推奨で存置（非破壊）。トップ公開（file facade star）。test +1・make check 緑（268）・test-heavy 緑（別途）。**Stage 4 完了（2026-07-02）＝公開型 `AsyncStore`＋`manystore.store` facade**: 公開型を `AsyncStreamingStore`→`AsyncStore`（`SyncStreamingStore`→`SyncStore`）に昇格＝**唯一の公開ストア型**（旧名は alias・`AsyncBufferedStore` は put/get だけの view として存置）。統合 facade `manystore/storage/store.py` を新設（AsyncStore/SafeStore/create_*_store/open_store/backend Store 群/registry を集約）＝トップ `manystore` へ全フラット公開＋`manystore.store` namespace。`manystore.kv`/`file` は deprecated alias で存置。生成 spec 再生成。make check 緑（269）・test-heavy 緑（44）・mkdocs --strict 緑。**残＝Stage 5（M073 spec/impl 分離）→ Stage 6（docs 更新）**。**Stage 3 完了（2026-07-02）＝アダプタ撤去（非推奨化）**: 合成は基底が持つので `KeyValueFileStore`/`KeyValueFromFileStore` は不要＝docstring に非推奨明記＋内部利用を撤去（conformance `_open_remote` は `RemoteStore` を直接 yield＝wrap 廃止）。クラスは後方互換で存置。make check 緑（269）。**Stage 2 完了（2026-07-02）＝BackendSpec 単一 factory**: `BackendSpec(name, factory, origin)`（kv_factory/file_factory 廃止）。`register_*` は旧 kwargs（kv_factory=/file_factory=）を `_resolve_factory` で単一 factory に写して後方互換受理。builtin seed を単一 `_make_X`（full Store 返却）に統合。`create_unsafe_store`=`spec.factory(**opts)`／旧 `create_unsafe_{kv,file}_store` は委譲。conformance `_build_filestore` も `spec.factory` に簡素化（`native` 分岐撤去）。make check 緑（269）・test-heavy 緑（44）。**Stage 1 完了（2026-07-02）＝backend 1 クラス化**: 両軸 native 基底 `StreamableBufferedStoreBase`（4 primitive すべて native・S3 用）を protocols に追加。各 backend を 1 クラスへ＝`DictStore`/`LocalStore`/`S3Store`（StreamableBufferedStoreBase 継承・multipart/range 保持）/`NatsStore`/`HttpStore`（read-only writer）/`RemoteStore`。冗長 FileStore（Dict/Nats）と KVS ビュー（Local の KeyValueFromFileStore 包み）を廃し、旧名（`S3KeyValueStore`/`S3FileStore` 等）は alias。make check 緑（268）・test-heavy 実 backend 緑（44 passed）。**残 b/c/d の設計＝`plans/m071-unify-store-plan.md`**（各段 alias で非破壊＋緑確認）。確定＝backend 1 クラス `S3Store` 等（旧名 alias）／公開型 `AsyncStore`（`AsyncBufferedStore` は view alias）／facade `manystore.store` 新設（kv/file は alias）／M073 は `manystore/spec/` 新設で本ミルストン実施。段階＝(1)backend 1 クラス化 (2)BackendSpec 単一 factory (3)アダプタ撤去 (4)公開型改名＋store facade (5)M073 spec/impl 分離 (6)docs 更新。**ステップ2（2026-07-02）＝合成を基底に内蔵し full Store 化**（ユーザー案「ラップ不要」）: `StreamingStoreBase` は既に IO→get/put を内蔵していた。対称に **`BufferedStoreBase` に open_reader/open_writer の既定合成**（read=全体 get・write=close で全体 put）を追加＝**両基底とも put/get＋open_* の全 Store 表面**を持つ。これで `DictKeyValueStore` 等 kv 寄りも別ラッパ無しで full Store（検証済）。native ストリーミング backend（S3 multipart 等）は open_* を override。make check 緑（267）。**残ステップ**＝(a) factory/顔を 1 本化（`create_unsafe_store`/`create_safe_store`/`open_async_store`・旧名は alias）／(b) `KeyValueFileStore`/`KeyValueFromFileStore` アダプタと backend の KVS/FileStore クラス対を畳む（native override は残す）／(c) `BackendSpec` を単一 factory に／(d) facade `manystore.kv`/`file` 統合／(e) M073（contract/impl 分離）と一括。**ステップ1（2026-07-02）＝コア Protocol/基底クラスを改名**（ユーザーが IDE リネーム・私が文字列/docstring/docs/生成 spec の取りこぼしを追随）: `AsyncKeyValueStore→AsyncBufferedStore` / `AsyncFileStore→AsyncStreamingStore` / `SyncKeyValueStore→SyncBufferedStore` / `SyncFileStore→SyncStreamingStore` / `KeyValueStoreBase→BufferedStoreBase` / `FileStoreBase→StreamingStoreBase`。scaffold の `base_name` 文字列も追随（`import` 生成が壊れないよう）。make check 緑（267）・test-heavy 実 backend 緑・mkdocs 緑。**⚠️過渡状態**＝Protocol/基底は Buffered/Streaming だが facade（`manystore.kv`/`manystore.file`）・factory（`create_unsafe_key_value_store` 等）・backend 名（`S3KeyValueStore`/`S3FileStore`）・アダプタ（`KeyValueFileStore`/`KeyValueFromFileStore`）・概念語「KeyValueStore/FileStore」は未改名。**残ステップ**＝(a) 公開 1 IF へ統合（独立 `KeyValueStore` を落とす or view/型エイリアス化）／(b) facade・factory・backend 名の整理／(c) M073（contract/impl 分離）と一括設計。ユーザー提案（2026-07-02）。**内部軸を「buffering（KV 本質）vs no-buffering（stream 本質）」に昇格**＝基底を `BufferingStore`/`NobufferingStore`（native がどちら向きか＝原則7 の「核は native 側」を型で表現・逆方向は合成）に再編。**公開は 1 インターフェースに畳む**＝put/get（KV API）と open_reader/open_writer（stream API）を同一 IF に載せ、独立した公開 `KeyValueStore` を落とす（実質 `FileStore=KVS+IO` を唯一の公開ストア〔名前は `Store` 等要再考〕へ昇格）。**思想整合**＝原則6「バッファ性が IF の本質」の昇格・fsspec `AbstractFileSystem`（cat/pipe＋open）先例・両方向合成は既存 `KeyValueFileStore`/`KeyValueFromFileStore` が実証済。**要 doc-first・大型**＝`protocols.py`/全 backend/全 surface（Safe*/sync/array）/conformancer/`docs/architecture.md` へ波及＋**公開 API 破壊**（`manystore.kv`/`manystore.file` の 2 facade 統合）ゆえ major bump 扱い。**命名確定（2026-07-02）＝`BufferedStore`/`StreamingStore`**。**未確定**＝(1) `KeyValueStore` を「put/get だけ見たい人向け view/型エイリアス」で残すか完全撤去か／(2) URL/registry 系（M068-70）が落ち着いた後に着手 |
| M073 | 仕様の集約＝構造契約（Protocol）＋挙動契約（conformance カタログ）を 1 つの spec 面へ | 相談 | ユーザー提案（2026-07-02）＝北極星「conformance=仕様の単一源泉」の帰結。**要は protocols.py の 2 役を分離**＝(A) 純粋な**契約/型**（Protocol・FileInfo/IfMatch/Verify）と (B) **既定実装**（`*StoreBase`・両方向アダプタ・IO・`_kv_copy`/`_sha256_hex`）。(A) を conformance の**挙動契約カタログ**（`ContractSpec`＋`assert_*`）と同居させ **spec パッケージ**（例 `manystore/spec/` or 昇格 `manystore/conformance/`）に束ね、そこから doc 生成（kv_spec/file_storage_spec/conformance_spec は既にここ由来）。**境界厳守**＝(B) 既定実装（runtime・全 backend が import）と**検証ハーネス**（`FileStoreTester`・fault-injection＝test-time）は spec に取り込まず層を保つ（runtime が test 機構を import しない）。**要 doc-first・大型・API 破壊**＝protocols.py を全所から import＝blast radius 大。**M071 と同じく protocols.py を再構成するので M071 と一括設計**（IF 統合＝BufferedStore/StreamingStore と、置き場の再編を 1 パスで）。関連 M022b（spec 検出）/M042（transport 整理）|

> **ゴール段階**: G1=配布できる（M005〜M008 完了）→ G2=安心して使える（M009〜M011・M016）→
> G3=機能十分（M012〜M015）→ G4=広く使える（M017 判断）。

### 完了マイルストーン（要点のみ・経緯は git 履歴）

- **M077（2026-07-02・完了）＝conformance provider を registry 駆動＋profile 宣言に**: ユーザー提案＝conformance に
  registry を参照させ store 構築を委ねる（ベタ実装削減）。`_build_filestore(backend, opts, native=)`＝`get_backend_spec`
  で `file_factory`（native）or KVS を `KeyValueFileStore` で wrap（既存 provider と同形）。`BackendProfile(id,
  backend, opts, native, gated, reachable, unsupported, setup)` の**宣言 1 つ**＋`_profile_opener` が
  construct→connect→cleanup を一元化。`all_providers`/`native_file_providers` を profile 駆動に（dict/nats/s3-real/
  s3-native）。**custom opener を残すもの**＝per-open リソース（local tmp・remote の in-process ASGI・s3-fake の
  client 差替・fault 注入）＝registry はテスト結線を知らない。**新 backend は「registry 登録＋profile 1 行」で
  conformance 自動参加**（`docs/implementing_a_backend.md` の「provider を足す」も profile 方式へ更新）。`make check`
  緑（267＋2 xfail）・**`make test-heavy` 実 backend 緑**（44 passed/seaweedfs CAS xfail・xpass＝既知 flaky）。
- **M074（2026-07-02・完了）＝conformance を real/fake/fault 切替＋backend 実装ガイドライン**: ユーザー要望
  「backend 再実装のガイドラインになる作り」を軸に。(1) fake を共有化＝`tests/fakes.py`（`FakeS3`/`FakeNatsObs`
  ほか・低層トランスポート模型。test_storage から移設し別名 import で温存）。(2) **s3-fake を非 gated provider
  化**＝adapter は本物・低層 aiobotocore client だけ fake（`_session` 差替）で **docker 無し fast に全 conformance
  契約を流す**（`copy_object`/`head_bucket`/`delete_object` を fake に追加）。s3-fake は 8 契約 pass＋CAS 2 xfail。
  (3) **権威の所在を明確化**＝`unsupported`＝`{put_if_absent,put_if_match}` で CAS を**非権威 xfail**（fake は
  単一プロセスで並行/CAS の意味論を再現しない＝認証は実 backend gated＋決定的 white-box に残す。ユーザーの
  「排他ロック等は fake と実機で違いうる」懸念への回答）。(4) **ガイドライン doc**＝`docs/implementing_a_backend.md`
  （Protocol→scaffold→参照実装 DictKeyValueStore→registry 登録→conformance matrix に provider 追加→契約を通す、
  の 5 ステップ＋real/fake/fault の守備範囲表）。nats-fake は JetStream メタ subject 忠実化が要るため **M076 へ
  follow-up**（嘘の温床を避ける）。`make check` 緑（267＋2 xfail）・mkdocs --strict 緑。
- **M075（2026-07-02・完了）＝CLI を Typer へ移行**: `__main__.py` の argparse を Typer に置換。`app`（`serve`）
  ＋ `store_app`（`store init`）。**typer を core 依存に**（`import manystore` では `__main__` 未読込＝typer 非
  import なので実行時コストは CLI 起動時のみ／console script `manystore` も追加）。重い依存（uvicorn/fastapi）は
  `serve` 内で遅延 import 継続。後方互換＝旧 `python -m manystore --config X`（先頭 `--config`）を serve に振る
  （`--help`/`--version` は素通し＝トップレベルのサブコマンド一覧）。`store init` の既存ファイルは `typer.Exit(1)`
  ＝`--force` で上書き。`main()` は Typer(click) standalone の成功時 `SystemExit(0)` を飲み込み非ゼロのみ伝播
  （プロセス終了コードは保つ・テストから `main(argv)` 直呼び可）。テスト +1（back-compat 振り分け）。
  `make check` 緑（258）。conformancer/server/gateway の各 `__main__` の Typer 化は後続（任意）。
- **M070（2026-07-02・完了）＝`manystore store init` ＋ 構成ファイルからストア復元**: neutral な
  `storage/config.py`（`ContextConfig`/`StoreConfig`/`parse_contexts`/`normalize_opts`/`load_store_config`/
  `find_config_file`/`discover_store_config`）を新設。**local の相対 `root` は構成ファイルのディレクトリ基準で
  絶対化**（cwd 非依存＝要件 (c)）。**上方向 discovery**（cwd から親へ `manystore.toml` を探す＝要件 (b)）。
  `open_store(target)` を拡張＝`://` 有→URL（M069）／無→**context 名**を discovery して解決（空文字=default_context・
  `config=` で明示可）。CLI をサブコマンド化＝`manystore store init [dir]` が雛形生成（要件 (a)・`--force`）、
  `manystore serve --config` は旧 combined（**旧 `manystore --config` は先頭フラグ検出で serve に振り後方互換**）。
  **serving の config は neutral を再利用**（`ContextConfig` 再export・`parse_contexts` 委譲＝drift 回避／
  `load_config` も base_dir=ファイルの dir に。⚠️副作用＝serving の local 相対も**構成 dir 基準**に変わった〔従来は
  cwd 相対〕＝`make ui` の dev ストアは `examples/.cache/…` になる。`.cache/` gitignore 済で無害）。トップ公開＝
  `open_store`（拡張）/`StoreConfig`/`ContextConfig`/`load_store_config`/`discover_store_config`/`find_config_file`。
  設計 `docs/store_config.md`（nav 追加）。テスト `tests/test_store_config.py`（10）。`make check` 緑（258）・
  mkdocs --strict 緑。残＝FileStore 版の URL/名前解決・path=prefix サブスコープ（後続）。
- **M069（2026-07-02・完了）＝名前 URL でストアを開く（fsspec 風）**: M068 registry の上に `open_store(url)`＝
  既存顔 `open_async_key_value_store` の URL 版（Safe＋接続 CM）。`storage/url.py` の純関数 `parse_store_url(url)
  -> (backend, opts)` が分解し委譲。**文法（doc-first・ユーザー確定 2026-07-02）**＝scheme=backend 名（registry
  解決）／**netloc=bucket（全 backend「1 store=1 bucket」統一）**／query=接続 opts。`local://.`=cwd・
  `local:///abs`／`s3://bkt?endpoint=&region=&access_key=&secret_key=&addressing_style=`（資格情報は query 可・
  未指定は boto 既定チェーン／env・config 推奨）／`nats://bkt?server=nats://h:4222`（server は query＝bucket と別
  レイヤ）／`http://h/base`＝URL 全体が base_url（例外・read-only）／`manystore://ctx?server=http://h/kv/raw`／
  未知 scheme=plugin backend 名として素通し。opts は**既存 flat kwargs 形**（`s3_bucket=`…）へ写す＝**既存 factory
  無改修・後方互換**（ネイティブ opts 整理・path=prefix サブスコープ・FileStore 版/名前解決は後続 M070 連動）。
  `open_store`/`parse_store_url` をトップ公開。設計 `docs/url_scheme.md`（nav 追加）。テスト `tests/test_url_scheme.py`
  （16）。`make check` 緑（248）・mkdocs --strict 緑。
- **M072（2026-07-02・完了）＝local 並行 delete レース修正＋contract を確定的ゲートに**: fast フルスイートで
  ~1/8 落ちる既存 flaky（`concurrent_delete_safe[local]`＝`FileNotFoundError: .../_conformance/cc/…`）を潰す。
  **真因＝local `delete` の TOCTOU**（`if path.is_file(): path.unlink()`＝並行 double-delete で is_file 通過後に
  別スレッドが消し `unlink` が生 FNF を漏らす）。修正＝`unlink(missing_ok=True)`（原子的・冪等。dir は
  `contextlib.suppress(IsADirectoryError)`）。**併せて `iter_all._scan` の per-file `stat` も同型 race を
  ガード**（走査〜stat 間に消えたファイルは一覧から除く）。**ユーザー方針「非一貫な挙動は確定的にテストを
  赤に」への対応の要点**＝当初は cross-backend 契約 `assert_concurrent_delete_safe` を rounds=40 反復で強化
  したが、**実 gated backend で nats が 390s→60s timeout を割る回帰**（1 round ~10s＝並行 get が M061 の
  bounded-get を踏む×40）。→**契約は単発・軽量に戻し（gated-safe）**、local 固有の TOCTOU/stat race は
  **決定的な white-box 単体テストを local 側に**置く方針に修正（probabilistic 多数反復に頼らない）:
  `test_local_delete_idempotent_under_toctou`（is_file を True 固定＋ファイルを先に消す＝旧は生 FNF・fix は no-op）/
  `test_local_iter_all_skips_file_vanished_mid_scan`（os.stat を「is_file=1回目 OK／ループ=2回目で欠損」に）。
  いずれも fix で緑・未fix で赤を実証。`make check` 緑（232）・full fast x5 flake 0・実 nats delete 10s。**併せて e2e ゲートを締めた**＝`make test-heavy` に
  `MANYSTORE_E2E_REQUIRED=1` を焼き込み（docker 未起動なら番兵が**赤**＝silent skip で緑を素通りさせない）。
  CI は step env を削除し e2e-up→test-heavy を叩くだけ＝local==CI。docker 無しで slow を見たいだけなら
  `uv run pytest -m slow` を直接叩く（fast/`make check` は従来どおり docker 不要・緑）。
- **M068（2026-07-02・完了）＝backend レジストリ / プラグイン機構（fsspec 風の土台）**: `storage/backends/
  __init__.py` の if/elif を `backends/registry.py` へ集約。**flat lookup ＋ tier/origin 分離 ＋ clobber 保護**
  ＝builtin（予約・shadow 不可）/ entry-point（group `manystore.stores`・遅延発見・既存名は拒否+warn）/
  programmatic（`register_backend`・`clobber=True` のみ上書き）。`BackendSpec(name, kv_factory, file_factory,
  origin)`。builtin 6 件を seed（memory/local/s3/nats/http＋**`manystore`＝`RemoteKeyValueStore` を seed**・
  file_factory=None＝KVS のみ。`ManystoreClient` は横断 SDK ゆえ非登録・コードは `client/` 据え置き）。重い依存
  （client 含む）は factory 内で遅延 import。`create_unsafe_*_store(backend, **opts)` は registry の薄いラッパへ
  （後方互換＝flat kwargs `local_dir=`/`s3_bucket=`… 温存。ネイティブ opts 整理は M069）。トップに `register_
  backend`/`BackendSpec`/`get_backend_spec`/`list_backends` を公開。core Protocol 変更なし。設計 `docs/backend_
  registry.md`（nav 追加）。テスト `tests/test_backend_registry.py`（builtin 解決・由来・clobber・EP 非 shadow・
  一覧）。`make check` 緑（230）・mkdocs --strict 緑。

- **M067（2026-06-30・完了）＝download の整合性検証（size 必須・hash あれば追加）**: client/download に
  検証が無かった（`DownloadCache.download` は whole get→書込のみ・client get も `r.content` 素通し）。
  **ビットフラグ `Verify(IntFlag)`**（NONE/SIZE/HASH/REQUIRE_HASH＋合成 DEFAULT=SIZE\|HASH・
  STRICT=SIZE\|HASH\|REQUIRE_HASH）で検証ポリシーを選べるように。`download(key, *, verify=Verify.DEFAULT)`
  ＝取得 bytes を `head()` の期待メタと照合し**検証してから書く**（cache に入るのは検証済みのみ・
  cache hit は再検証しない・`Verify.NONE` は head() も引かない）。size は全 backend の `head().size` で
  完結／hash は `FileInfo.sha256`（best-effort＝無ければスキップ・REQUIRE_HASH なら失敗）。不一致は
  新例外 `IntegrityError`（status 422）。`Verify`/`IntegrityError` をトップ export。**残 B＝hash メタ充填は
  2026-07-02 完了**（put 時 sha256 を native メタへ→head 露出→conformance `meta.sha256_correct`。M013残 参照）。
- **M061（2026-06-30・完了）＝実 backend e2e を CI で gated 実走（skip 許容やめ）**: docker compose で nats /
  seaweedfs / **minio** を起こし、`ci.yml` に e2e ジョブ追加（`make e2e-up`→`MANYSTORE_E2E_REQUIRED=1
  make test-heavy`→`make e2e-down`。ubuntu runner 同梱の docker+compose を直接叩く＝local==CI）。**核は
  skip マスクの撤去**＝`test_conformance_matrix._store` の `except Exception`→`pytest.skip` を外し、**未到達
  のみ skip／到達できる接続・契約の失敗は伝播**（gated の実バグ・能力差を赤/xfail で表に出す）。実走で
  2 つの実問題を炙り出して是正: ①**nats `concurrent_delete_safe` ハング**＝`obs.get` がチャンク購読で
  待つ間に並行 delete の `purge_stream` がチャンクを消し「来ないチャンク」を無期限待ち（nats-py に read
  timeout 無し）→ `get_or_raise` を `wait_for(_GET_TIMEOUT_S=10s)` で境界化し、タイムアウト時は実在再確認で
  「消えていれば NotFound（delete にレース負け＝契約許容）／残存なら伝播（fail-loud）」に振り分け。
  ②**SeaweedFS は条件付き PUT（CAS）を保証しない**＝同時 create が**時々**二重成功（**flaky**＝強制したり
  しなかったり・非決定的）。**conformancer を s3 実装ごとのマトリクスに**（`conformance_providers.S3_IMPLS`＝
  seaweedfs/minio。MinIO は CAS を満たす＝実機 6/6 実証）。実装の能力差は provider の `unsupported` から
  **`xfail(非strict)`**＝暗黙 skip でなく明示の行に出す（flaky ゆえ strict にしない＝XPASS のたびに CI を
  割らない・XFAIL/XPASS の揺れ自体が「保証なし」を物語る）。CI 必須時は `test_e2e_backends_reachable_when_
  required` が起動漏れを赤にする。slow 41 passed / 2 xfailed（seaweedfs CAS）/ 9 skipped（virtual ほか）。
  **`make test-heavy` に per-test 目標時間**（`pytest-timeout`・既定 `TEST_HEAVY_TIMEOUT=60s`）＝将来の
  ハングを stack 付きで打ち切り「必要以上の待機」を防ぐ backstop（async の await 中の停止も signal method で
  中断＝実証済）。`make check` 緑（215）・mkdocs --strict 緑。
- **M065（2026-06-28〜29・完了）＝conformancer を「仕様の単一源泉」に育てる（北極星①〜④）**: 実装漏れを
  conformancer に契約として実装し backend 横断で検知。`FileStoreTester` に **run_middle/heavy/full** を実装
  （差分契約＝DictFileStore をオラクルに観測一致）＋**オラクル非依存の絶対契約**（`ABSOLUTE_CONTRACTS` カタログ）
  ＝writer all-or-nothing（M058 を契約先行修正）・CAS 並行（create/update）・**非CAS 並行**（`assert_concurrent_
  overwrite_atomic`＝無条件並行上書きの原子性／`assert_concurrent_delete_safe`＝並行 delete 冪等・get は seed か
  NotFound）・**fail-loud**（fault-injection で「障害を欠損/False/default/正常終了に化けさせない」を契約化＝
  in-process `assert_fail_loud_propagation`／transport `assert_fail_loud_over_transport`＝HTTP Remote＋実 leaf
  nats/s3 の下層を故障プロキシに差し替え）。北極星＝**①テスト②pytest-cov 可視（`make cov`・TOTAL 77%＝M059）
  ③spec 文書生成（`docs/conformance_spec.md`・絶対契約は宣言／差分観点は run_* 実行から導出）④scaffold
  （`--scaffold`＝契約一覧が実装の TODO）**。`run_full`＝差分＋全絶対契約を 1 レポートへ集約。**この契約群が
  nats `delete` の握り潰し（`suppress(Exception)`→`except JSNotFound`）と `_S3MultipartWriter.__aexit__` の
  all-or-nothing 違反（→`_abort()`）を炙り出し是正**。実 nats/s3-path/native で緑。`make check` 緑（215）。
- **M066（2026-06-28・完了）＝挙動契約の集約ハーネス**: IF が揃うので同一契約 body を全実装へ流す。
  `tests/conformance_providers.py` に **全 provider を 1 か所宣言**（dict/local/remote/実 nats/s3-virtual/s3-path
  ＋leaf-fault＋native-file）→ `tests/test_conformance_matrix.py` が全契約をパラメタ化して流す。run_light/middle/heavy
  を **delete_all 全消去から uuid 名前空間スコープ＋後始末（`_cleanup_ns`）へ**転換＝**非破壊**ゆえ共有 backend
  （実 nats/s3）でも安全に実行。⚠️既知の弱点＝gated provider は contract の AssertionError も skip に化けうる
  （`_store` の except・健全時は real PASS でカバレッジは効く）。
- **M054〜M064 品質監査（2026-06-28〜29・完了）＝2026-06-28 の 4 観点監査で抽出**: M054（nats `get_or_raise`
  fail-loud＝`except JSNotFound` narrowing）・M055（remote `exists` の 404/5xx 区別）・M056（nats `_get_obs` の
  double-checked lock＝接続リーク防止）・M057（connect/aclose の `_connect_all`/`_aclose_all`＝部分確立を巻き戻し・
  全件クローズ）・M058（`_KvWriteFileObject.__aexit__` の all-or-nothing）・M059（pytest-cov・`make cov`・TOTAL 77%）・
  M060（crypto.py を pytest 化＝inline `_selftest` を撤去し `tests/test_crypto.py`・test +9）・M062（`list_all` の
  基底集約＝override 13 箇所削除・47 行減）・M063（`DownloadCache.download` の同期 IO を anyio offload）・
  M064（array cp/mv の identity 判定＝保守設計を docstring 明記＋テスト固定）。
- **M046（2026-06-28・完了）＝conditional put / CAS**: put の lost-update を **派生メソッドを作らず put 1 本＋任意
  `if_match`**（ユーザー確定）で実装＝None=LWW／`FileInfo.absent()`=create-only／`FileInfo`=update CAS（不一致
  `ConflictError`）。**opaque version 文字列は出さず比較トークンは `FileInfo.etag` に畳む**（S3=ETag・local=
  os.link/flock+mtime_ns+size・dict=世代 seq・NATS=メタ subject の最終 stream seq＋`Nats-Expected-Last-Subject-
  Sequence` でサーバ側原子 CAS）。`head(key)->FileInfo` が version 読み口。**全 backend＋native REST（routes の
  If-None-Match/If-Match→if_match・HEAD で ETag 露出）＋remote（条件ヘッダ＋head override）＋S3 GW（If-Match は
  MD5 突合＋head version の二段橋渡し・不成立 412・非対応 501）**まで配線。conformancer の create/update CAS 並行
  チェッカを実ストア（Dict/Local）＋HTTP 越し＋実 nats で機械検証。M043 lockstep 全揃え。
- **M053（2026-06-27・完了）**: 「欠損」を例外ファミリへ昇格＝**`NotFoundError(FileNotFoundError, ManystoreError)`**
  （status=404・title="Not Found"）を新設（ユーザー指摘＝tests が exceptions.py 定義でない生 `FileNotFoundError`
  を想定していた）。stdlib を先頭に残すので既存 `except FileNotFoundError`/`pytest.raises(FileNotFoundError)` は
  継承で全通り（破壊変更ゼロ）。src の生 FNF を全て NotFoundError へ（protocols `_kv_copy`・memory/nats/s3/http/remote
  の get_or_raise・local の `mv`／**`open_reader` の OS 生 FNF**・**s3 native `open_reader` の NoSuchKey**＝streaming 経路も
  正規化）。トップ export 追加。tests は `pytest.raises(NotFoundError)` へ厳格化（test_storage/conformance/ui・fake も）。
  `_STDLIB_PROBLEM` の FNF 行は生 FNF fallback 用に残置。
- **M052（2026-06-27・完了）**: テストを pytest-asyncio（`asyncio_mode="auto"`）へ一括移行＝`asyncio.run(scenario())`/
  直接 `asyncio.run(coro)` 包み **75 箇所**を `async def test_*`＋`await` に展開（test_storage 55・conformance 10・
  e2e 1 ほか）。挙動・件数完全不変（133 passed/2 skip）。陳腐化した過渡コメントを除去。今後の新規テストは async def が標準。
- **M049（2026-06-27・完了）**: `create`（create-if-not-exists）を **`_StoreBase` の既定実装**として追加（cp/mv と
  同列の*非原子の派生*＝backend primitive ではない）。既存なら `ConflictError`。exists→put で組む＝TOCTOU で
  並行二重作成しうる（原子版は M046 `put_if_absent` が正本＝役割分担）。async/sync 両 Protocol＋sync_bridge に追加し
  M043 lockstep（parity assert）を維持。Safe 越しでも override 済み exists/put を呼ぶのでキー検証が透過。
- **M050（2026-06-27・完了）**: 2 ストア片方向同期 `StorageMirror` を新パッケージ `storage/sync/` に新設。source→sink を
  **集合差 reconcile**（両側 iter_all を突合）で create/update/skip 分類＋`prune=True` で delete。source が常に正・
  sink→source 書き戻しなし（one-way）。`compare`（既定 size 比較）で無駄更新スキップ。`plan()`=dry-run / `sync()`=適用。
  kv facade＋トップへ export。**残＝M051 で k8s を具体 sink に**（apply 変換は sink backend の put に内包）。
- **M048（2026-06-27・完了）**: 例外を `exceptions.py` に集約＝**`UnsupportedOperation(io.UnsupportedOperation,
  ManystoreError)`**（status=405・stdlib を先頭に残し `except io.UnsupportedOperation`/FileObject 慣習を維持しつつ
  HTTP status を持たせる）＋ **`ConflictError(ManystoreError)`**（status=409・M046 用）を新設。生 `io.UnsupportedOperation`
  raise を全廃して manystore 版へ（protocols/local/http/s3/ipfs/crypto・計 10 箇所）＝「例外は exceptions に
  HTTP status 付きで集約」をユーザー方針として確立。tests は `io.UnsupportedOperation` subclass ゆえ無改修で緑。
  **追補（ユーザー方針）**: 任意例外→problem の変換ロジックを **基底メソッド `ManystoreError.problem_for`**
  （classmethod）へ集約＝変換の正本を基底に置く。モジュール関数 `to_problem` はそこへの薄い委譲（後方互換）。
- **M047（2026-06-27・完了）**: CI/Makefile/mkdocs を supervisor 新標準へ追従（下り dispatch・急がない）。
  ①`pages.yml`＝deploy と Upload Pages artifact の guard を `github.event_name == 'push' && github.ref ==
  'refs/heads/main'` へ（PR run の環境保護落ちを恒久回避）＋`setup-uv@v6`（[[func-mkdocs]] 雛形が正本）。
  ②Makefile をテスト 4 段＝`test`=`not slow and not benchmark`・`test-heavy`=slow・`test-benchmark`=benchmark・
  `test-all`=全部（`.PHONY` 追補）。③pyproject markers に `benchmark` 追加。④`ci.yml`＝`checkout@v5`/`setup-uv@v6`
  へ（Node20 廃止 warning 解消・R17）。⑤mkdocs `--strict` 緑確認（spec 再生成で tracked 変化なし）。
  `make check` 緑（fast 126）・`test-all` 137 passed 1 skipped。benchmark 該当無し＝`test-benchmark` は exit 5（許容）。
- **M043（2026-06-27・完了）**: ABC 基底 ↔ Protocol の lockstep を是正＝是正案①+②（supervisor 指示）。
  ①**基底に共通表面を全面宣言**＝`protocols.py` に共通基底 `_StoreBase(abc.ABC)` を新設し、
  `KeyValueStoreBase`/`FileStoreBase` の双方が継ぐ。`_StoreBase` は abstract primitive
  （put/get_or_raise/iter_all/exists/delete/connect/aclose）＋既定実装（get/list_all/cp/mv）を 1 か所に持つ
  ＝部分実装は**インスタンス化時 `TypeError`**（fail-loud）。`FileStoreBase` は open_reader/open_writer を
  abstract に足し get_or_raise/put を IO から導出。②**conformancer に base↔Protocol parity assert**
  （`base_protocol_parity_errors`/`assert_base_protocol_parity`）＝Protocol 全メンバの網羅＋シグネチャ一致を
  機械チェック。test で `KeyValueStoreBase↔AsyncKeyValueStore`・`FileStoreBase↔AsyncFileStore` を点検。
  **波及**: 共通 abstract を _StoreBase に上げた結果、mixin 後置だった backend（http/s3/nats/ipfs）は
  abstract が concrete mixin を MRO で隠して生成不能になったため、宣言を `(_XBase, KeyValueStoreBase)` へ
  並べ替え（mixin 先置の定石）。横展開ゲート（IPFS/LB 本体は M043 前提）の前提を満たした。
  **追補（2026-06-27・ユーザー要望）＝conformancer↔Protocol drift ガード**: conformancer の `_OPS`/`_op_*` は
  Protocol の呼び出し方を直書きする＝protocols.py が進化すると「古い契約を前提に黙って誤検証」しうる。
  汎用 `signature_drift(protocol, expected)` ＋ conformancer が叩くメンバのシグネチャ写し
  （`_PINNED_*_SIGNATURES`）＋ `assert_conformancer_protocol_current`（**protocols.py が正**・不一致は
  conformancer が古い合図＝`_op_*` と写しを追従）。逆方向（conformancer 先行）は想定しない。fast 126 passed。
- **M038（2026-06-26・完了）**: `manystore/crypto.py` 新設＝ストリーム暗号と FileStore IO への繋ぎこみ IF を明確化。
  primitive **`StreamCipher`**（`transform(offset, data)`＝オフセット指定・チャンク境界非依存の対称変換）＋参照実装
  `XorStreamCipher`（繰り返し鍵 XOR・**安全でない** placeholder）。`AsyncFileObject` を包む **`CipherReader`/`CipherWriter`**
  （read で復号 / write で暗号化・自身も `AsyncFileObject` を満たす＝`open_reader`/`open_writer` の戻り値にそのまま被せる）。
  **ストア実装なし・tests 未配置**（インライン `_selftest`＝`python -m manystore.crypto` で round-trip/境界非依存を確認。
  後で tests へ移す前提）。ユーザー要望＝IF の明確化に限定。

- **M001〜M004**: 旧 `shoudou_storage` 残骸掃除 / 実 backend E2E（NATS・S3 path）/ CI＋lint 統一 / README。
- **M005〜M008**（配布前提 G1）: 未使用依存 `redis` 削除 / LICENSE=MIT / PyPI メタ整備。**M007 py.typed は不採用**
  （型チェッカが公開 API を厳格化し運用コスト増＝ユーザー判断）。
- **M009**: 統一例外階層 `ManystoreError`（`manystore/exceptions.py`）＝`status/title/type`＋`to_problem` で RFC 9457
  Problem Details に変換。native REST のエラー応答を `application/problem+json` 化（S3 GW は S3 互換 XML のまま）。
- **M018**: HTTP backend（read-only・`backends/http_store.py`・httpx 遅延 import）。
- **M019**（UI P1〜P3）/ **M020**（UI パンくず＋生パス編集）: 完了。残 P4(http RW)/P5 等は M021 等へ移管（plan は GC 済）。
- **M021 S1/S2**: S3 ゲートウェイ + Multipart（予約キー空間 `.manystore-mpu/...` で状態管理）。残は上表 M021残。
- **M022 P1/P2**: conformance メソッド存在チェック＋`FileStoreTester.run_light`。残は上表 M022b。
- **M023**: native REST + S3 を単一 FastAPI に統合（`include_router(prefix=)`・共有 service 単一 lifespan）。
- **M025 フェーズ1＋改**: 名前空間を buffer 性で再編（`/kv/raw`・`/storage/s3`）＋native を `{bucket}/{path}` addressing に。残は上表 M025残。
- **M027 / M027c**: Local の KV を `KeyValueFromFileStore(LocalFileStore)` 派生に（真実は FileStore 側に集約）。
  get_or_raise primitive 化を client/service へ波及（`KeyValueStoreBase` を ABC 化）。
- **M028**: HTTP の context を `ArrayKeyValueStore` バックに（mount で振り分け・横断列挙を委譲）。`plans/` から削除済。
- **M030**: prefix を `SupportsPrefixListing` capability に移設（直後 M036 で fail-loud 化＝暗黙フォールバック撤去・`scan_prefix` 明示 opt-in）。
- **M031**: `conformance.py`→`conformancer/`（ユーザー IDE refactor）。残＝内部分割の整理。
- **M034（2026-06-25・完了）**: conformancer に CLI 入口 `python -m manystore.tools.conformancer`（`__main__.py`）を新設。
  メソッド存在チェック（接続不要・決定的）で各実装 × メソッドの Implemented/Not を `docs/kv_spec.md` /
  `docs/file_storage_spec.md` へ生成。`make conformance-docs` でキック。挙動ベースの spec 検出は M022b に残置。
- **M035**: 実装を `manystore/stores/` へ分類（base/array/safe/sync_bridge）＋`conformancer/`。完了 plan 削除。
- **M037**: テスト軽重分離（`@pytest.mark.slow`・`make test`/`test-all`）＋未整備依存の早期 skip。fast ~0.65s。
- **protocols.py 集約（2026-06-25）**: `stores/base.py` 削除＋既定実装を protocols.py へ全面集約（詳細は systemPatterns）。
- **M033（2026-06-25）**: `iter_all`/`list_all` の limit 統一は全面波及済と確認＝全 backend・全ラッパ
  （Safe/Array/sync_bridge/remote）が `limit:int|None` シグネチャで forward、`list_all` は `iter_all(limit)` 参照。
- **M036（2026-06-25・完了）**: error-swallow を fail-loud 化。`nats.iter_all`（空ストアの `NotFoundError` のみ []・
  他は伝播）／`nats.exists`・`s3.exists`（not-found〔NotFoundError／404〕のみ False・認証/5xx/接続断は伝播）。
  test +3（s3 非 404 伝播・nats 空は非エラー・nats 実エラー伝播）。watcher ポーリングは意図的レジリエンスとして存置。
  ※nats の not-found catch 自体を将来 `obs.watch()` ベース再実装で取っ払う TODO をコード内に残置（ユーザー判断で
  今回は現状維持＝not-found→[]/False の正規化は契約上必要）。
- **M024（2026-06-25）**: pull 型エスカレ（outbox）の文書追従完了＝MB に push 前提の残記述なし・旧スキル名なし・
  alias を `[[unit-quality]]` に統一。
- **M011（2026-06-26・完了）**: 安全入口の最終形＝**入口の命名マトリクスを確定**（2 コミット）。
  - **②責務分離（C1）**: `ArrayKeyValueStore.mount`/`unmount` を**登録のみ（I/O なし）**に分離（mount が
    connect も担う二重責務を解消）。接続は顔 `open_async_array_store(mounts)` の CM が一括で担う。`StorageService.connect`
    は明示 connect + 同期 mount に追従。
  - **①命名（C2）**: 低レベル factory を `create_key_value_store`/`create_file_store` →
    **`create_unsafe_key_value_store`/`create_unsafe_file_store`** にリネーム（名前で unsafe＝キー検証なしを明示）。
    **`create_safe_{key_value,file,array}_store`** 新設（Safe 包装のみ・未接続）。**生口はトップ公開に残す**
    （ユーザー確定＝格下げせず名前で明示のみ）。caller 全追従（connect/service/config/README/tests）。test +1。
  - 完成した 3×3 マトリクス: **unsafe**（生・未接続・キー検証なし）/ **safe**（Safe 包装・未接続）/
    **open_async**（顔＝Safe 包装＋接続 CM）× kv/file/array。open_async は内部で create_safe_* を呼ぶ（dedup）。
- **M010（2026-06-25・完了）**: local backend を非ブロッキング化＝`storage/backends/local.py` の同期 IO
  （open/read/write/close・rglob+stat・replace/unlink・mkdir）を `anyio.to_thread.run_sync`（`_offload`）で
  ワーカースレッドへオフロードし event loop を塞がない。`_LocalAtomicWriter` は構築（mkstemp/fdopen）も
  syscall ゆえ async ファクトリ `_LocalAtomicWriter.open()` 経由に変更（atomic temp+replace は不変）。
  方式は **anyio**（スレッドプール系・新規依存ゼロ＝httpx 経由で在中だが明示依存に格上げ `anyio>=4.0.0`）。
  真の async disk IO（aiofile/libaio）は不採用＝移植性・最小・YAGNI 優先（buffered では native AIO も
  スレッドへフォールバックし実効差小）。`__init__` の resolve/mkdir は構築時一回限り＝ホットパス外で据置。
- **M032（2026-06-25・完了）**: 安全な入口（ライブラリの顔）を新設＝`open_async_key_value_store` /
  `open_async_file_store`（トップ公開）。**Safe 包装必須の接続 CM**（`async with` で connect＋`Safe*` 包装、
  終了で aclose。`policy`/`verify` も受ける）。併せて `create_file_store`（FileStore 版ファクトリ）新設と
  `SafeFileStore` を `SafeKeyValueStore` 継承に作り直し（= KVS 面も検証付き＝M027b の Safe 残も解消）。
  生ストアは `create_*`/`connect_*`（低レベル）に残置。test +4。

## 現状ステータス

独立ライブラリ化＋配布前提（G1）完了。コア抽象は「FileStore=KVS+IO・核は native primitive 側・get duality・
prefix は core 引数・conditional put/CAS」で安定。protocols.py が契約＋既定実装の単一源泉。**品質優先フェーズ**
（2026-06-28〜30）＝4 観点監査の品質強化（M054〜M064）・**conformance を仕様の単一源泉に**（M065/M066＝北極星
①〜④＋fail-loud＋非CAS並行＋run_full）・**M061**（実 backend e2e を CI で gated 実走＝skip 許容やめ・s3 実装
マトリクス・nats 並行 delete/get 修正）まで完了＝**品質強化フェーズ完了**。残＝機能・完成度（M012/M013/M021残/
M025残 等）。拡張（M051/M039/M040/M026/M045）は方針どおり後回し。fast 215 passed・slow 41 passed/2 xfailed。

## 既知の問題

- `s3-virtual`（ドメインスタイル）はローカル S3 互換では `bucket.<host>` を名前解決できず常に skip。
  **virtual-host の仕様上の制約**（実 AWS 等の DNS 環境向け）であり未解決バグではない。
- **SeaweedFS は条件付き PUT（CAS）を保証しない**＝`put_if_absent`/`put_if_match` の並行契約で同時 create が
  **時々**二重成功（**flaky**＝強制したりしなかったり・非決定的）。**実装の能力差**（バグでなく backend 仕様）
  ＝conformance では `S3_IMPLS` の `unsupported` で `xfail(非strict)` 宣言（flaky ゆえ strict 不可）。CAS が
  要るなら MinIO / 実 AWS S3 を使う（実機検証済）。
- `make test`（fast）は lint を回さない＝format ドリフト（特に CJK 行の E501）は `make format` でしか出ない。
- **e2e の seaweedfs は `make e2e-up` 必須**（`docker compose up` 直叩きは不可）＝`weed shell` の S3 identity
  登録ステップを含む。未登録だと接続時 `HeadBucket 403 Forbidden` で seaweedfs 系が総崩れ（2026-07-02 に遭遇）。
  MinIO は既定資格情報（minioadmin）で動くのでこの手当て不要。

## 意思決定の変遷

- **atomic write は torn-write 防止であり排他制御ではない／並行更新は conditional put で別途**（2026-06-27 方針→
  **M046 で実装済**）: local の temp+`os.replace` は torn を見せない原子性のみ保証し、並行 put の lost update は
  検出しない（LWW）。検出は **CAS を opt-in の conditional put として `ConflictError` で raise**（put 既定の無条件
  set 契約は維持＝最小-core）。**比較トークンは opaque な `version:str` でなく `FileInfo.etag` に畳んだ**（当初の
  opaque version 案から訂正）＝S3=ETag・NATS=メタ subject の最終 seq・**local=mtime_ns+size**（modern FS は ns 精度で
  etag 的に使える・ユーザー合意）。難所はトークン選択でなく CAS の原子性＝local は os.link（create）／flock+stat
  比較+replace（update）で直列化し TOCTOU を避ける。全 backend＋REST/remote/S3 GW まで配線し conformancer が並行
  安全性を機械検証（M065 の絶対契約）。
- **`put` は共通レスポンス `FileInfo`（`{filename,size}`）を返す**（2026-06-26）: 全 backend が追加 I/O なしに生成できる
  最小・共通の *file メタデータ* のみ。revision/etag は共通でないため core には載せない。
- **prefix 列挙を core の `iter_all(prefix="")`/`list_all(prefix="")` 引数に畳む（capability 廃止）**（2026-06-26・ユーザー判断）:
  旧 `SupportsPrefixListing` / `iter_prefix()` ディスパッチ / `scan_prefix()` を全廃。S3 はサーバ側 `Prefix=` で native、
  native の無い backend（local/dict/nats/remote）は scan+filter を **契約上の既定動作**として実装。これにより
  **fail-loud-for-prefix（要求7 / M030）は意図的に撤回**（scan+filter は「隠れた fallback」ではなく明示の既定）。
  native REST API は従来通り prefix 非対応＝`RemoteKeyValueStore` は client 側 scan+filter（S3 gateway のみ prefix native）。
- **backend 生レスを運ぶ封筒（request/response 型）は却下**（2026-06-26・ユーザー判断）: パラダイム不一致。
  request/response・pub/sub は非ターゲット（正本は projectbrief「非ターゲット」）。dispatch メソッド程度は余地あり。
- ストレージ抽象は独立ライブラリとして自己完結。利用側固有の結線は利用側 adapter に閉じ、本体は最小・汎用に保つ。
- **S3 アドレッシングスタイルを明示パラメータ化**（既定 virtual／利用側が `"path"` opt-in）。fake では気づけず実機 E2E で露見。
- **Python 3.14+ 前提に確定**: PEP 649（注釈遅延評価）が既定ゆえ前方参照は valid＝`from __future__ import annotations`
  全廃。`requires-python>=3.14`＋ruff `target-version=py314`。ruff は py314 対応必須＝`RUFF_VERSION=0.15.18`。
- **fail-loud（要求7）**: 暗黙フォールバックで失敗・非対応を握り潰さない。capability 非対応は loud 失敗・非 native は明示 opt-in。
- **protocols.py = 契約＋既定実装の唯一の源泉**（2026-06-25）: backend が継承・流用する base/adapter/helper を 1 ファイルに集約し
  二重参照を断つ（`stores/base.py`・`sync_storage.py` 削除）。
- **ディレクトリを 3 バケットに再編**（2026-06-25・ユーザー IDE）: `storage/`（ライブラリ本体＝backends・surfaces〔旧 stores〕・
  facade kv/file）/ `serving/`（HTTP 公開＝services〔旧 implement〕・server・gateway）/ `tools/`（conformancer）。`implement`
  の曖昧な名前を解消し「ストレージ抽象」と「その serving」をトップで分離。`protocols`/`connect`/`exceptions`/`client`/`combined` はトップ据え置き。
- **Python サポート範囲は 3.14+ で確定（M017 見送り・2026-06-25）**: 3.10+ へ広げる案は YAGNI で見送り
  （広げると future import 復活＋ruff 設定の負担。3.14 純度を優先）。需要が出たら再検討。
- Memory Bank: Cline 準拠 6 コア。作業フォルダ `.work/skills/flow-memory-bank/`（`.work/` は commit する正本）。
  完了 plan は削除し、残フェーズの plan のみ `plans/` に保持。
