# Progress

## 動くもの（What works）

- 2 ストア抽象（`KeyValueStore` / `FileStore`）と backend 実装（local / s3 / nats / **http** / **dict(memory)**）。
  http は **read-only**（`get`/`exists`/`open("rb")` のみ。書き込み・一覧は `io.UnsupportedOperation`）。
  モジュールは stdlib `http` と紛れないよう `backends/http_store.py`（backend 識別子は `"http"`）。
  **dict(memory)** は `DictKeyValueStore`/`DictFileStore`（`backends/memory.py`・依存ゼロ・接続不要・揮発。
  `create_key_value_store("memory")`）＝テストの参照 backend／軽量一時ストア。S3/NATS/HTTP/dict すべて **完全な
  FileStore（KVS+IO）に準拠**。設計原則の正本は **`docs/architecture.md`**。
- **適合性ツール（M022 P1+P2）**: `manystore/conformance.py`。① メソッド存在チェック（`assert_key_value_store`/
  `assert_file_store`）② 挙動契約チェック（`check_key_value_store_contract`/`check_file_store_contract`＝接続済み
  ストアを叩き backend 非依存の振る舞いを検証・read-only は `writable=False`）。`test_e2e_backends.py` は実 backend に
  この契約を注入（重複なし）。サードパーティ backend が pytest から横断準拠確認できる。シグネチャ検査は未実装。
- **Protocol 関係: `FileStore = KeyValueStore + {open_reader, open_writer}`**（`FileStore(KeyValueStore, Protocol)`）。
  KVS は FileStore から IO を除いた部分集合。**2 方向の汎用アダプタ**（`async_storage`）: `KeyValueFileStore`
  （KVS→FileStore＝IO を合成し KVS 面は委譲＝完全な FileStore）/ `KeyValueFromFileStore`（FileStore→KVS＝IO を
  落とし残りは委譲）。**local は `LocalFileStore` が完全な FileStore（KVS+IO）の真実の実装**で、
  `LocalKeyValueStore = KeyValueFromFileStore(LocalFileStore)` の薄い KVS ビュー（M027）。
- **KVS の get は get_or_raise primitive**: 共有基底 `KeyValueStoreBase` が `get(key, default=None)` を
  `get_or_raise`（欠損で `FileNotFoundError`）から 1 か所で実装。backend は get_or_raise だけ実装（try/except 不要）。
  Local(アダプタ)/S3/NATS/HTTP/Safe/Array/DownloadCache＋sync ブリッジが準拠（client/service は M027c で残）。
- async / sync / bridge（`AsyncToSyncKeyValueStore`）。
- 接続ライフサイクル（`connect_key_value_store` / `connecting` / `ConnectPolicy`）。
- 安全パス（`validate_safe_path` / `SafeKeyValueStore`〔download・キャッシュ含む〕 / `SafeFileStore`）。
- 合成ストア（`ArrayKeyValueStore` / `DownloadCache`）。
- **テスト**: `uv run pytest` で **51 passed, 1 skipped**（S3 / NATS / HTTP は in-memory fake で検証）。
- **CI**: GitHub Actions（`.github/workflows/ci.yml`）で push/PR 時に `make check`（ruff format-check + check + pytest）。
- **実 backend 疎通**: NATS / S3（path-style）を実機 E2E で検証済み（`tests/test_e2e_backends.py`、`make e2e-up`）。
  パラメタライズで local / nats / s3-virtual / s3-path に同一 CRUD を注入。`make check` で 47 passed, 1 skipped
  （s3-virtual はローカル S3互換では原理的に skip）。
- **ストレージ UI / サーバ（M019 P1〜P3）**: `manystore.{implement,server,client}` の 3 層。任意 context を
  HTTP+WS で公開する汎用 CRUD UI。`manystore[server]` extra（fastapi/uvicorn/watchdog・遅延 import）。
  REST/WS は `KeyValueStore` と 1:1、`SafeKeyValueStore` でキー検証、PollingWatcher で WS ライブ通知、
  config の `views.featured` で重点パスを pin/quick_write（interrupt も汎用 PUT で投入）。`RemoteKeyValueStore`
  でサーバ越しの KVS。同梱ビルドレス Web UI。`make check` で **59 passed, 1 skipped**。実起動スモーク済み
  （`python -m manystore.server --config examples/manystore-ui.toml`、interrupt への remote PUT 往復を実証）。
- **S3 互換ゲートウェイ（M021 S1+S2）**: `manystore.gateway`（`create_gateway` / `python -m manystore.gateway`）。
  GET/PUT/HEAD/DELETE/ListObjectsV2 + Multipart を `StorageService` 上に 1:1 合成（コア IF 不変）。
- **統合エントリポイント（M023・名前空間は M025 で再編）**: `manystore.combined.create_combined_app` /
  `python -m manystore`。native REST/WS と S3 ゲートウェイを `include_router(prefix=...)` で 1 アプリに束ね、
  共有 service を単一 lifespan で 1 回だけ connect/aclose。第1階層は **buffer 性で分ける（M025）**＝
  `/kv/raw`=native REST/WS（buffered）・`/storage/s3`=S3 ゲートウェイ（streaming）。S3 クライアントは
  `endpoint_url=<host>/storage/s3`。単体アプリ/単体起動は後方互換で維持。`make check` で **91 passed, 1 skipped**。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

| ID | タスク | 状態 | 備考 |
|----|--------|------|------|
| M001 | 旧 `shoudou_storage` 残骸の掃除（docstring/コメント） | 完了 | NATS 既定バケット `shoudou_files`→`manystore_files`（既定値のみ・テスト非依存）|
| M002 | 実 backend（S3 / 実 NATS）での E2E 疎通検証 | 完了 | NATS / S3(path) を実機 E2E で検証。`make e2e-up` が SeaweedFS に dev identity（`weed shell s3.configure`）を登録し、`make check` で s3-path も通る（47 passed, 1 skipped）。s3-virtual はローカルでは原理的 skip |
| M003 | CI（GitHub Actions）＋ lint/format 統一 | 完了 | `.github/workflows/ci.yml`（setup-uv→`make check`）。supervisor 指示で着手。あわせて **Python 3.14+ 前提**を確定（後述） |
| M004 | README / ドキュメント整備 | 完了 | ルート `README.md` 作成（特徴・install・quickstart・backend別接続・ConnectPolicy・Safe・開発/CI/3.14）|
| M018 | HTTP backend（read-only）追加 | 完了 | **ユーザー要望**。GET/HEAD で取得する read-only ストア（`get`/`exists`/`open("rb")`）。`backends/http_store.py`（stdlib `http` 回避でリネーム）、`create_key_value_store("http", http_base_url=..., http_headers=...)`、`__all__`/README/テスト整備。httpx を遅延 import。51 passed |

### 評価で洗い出した改善バックログ（2026-06-21、目標 G1〜G4）

優先度: 高=安く無害高効果/配布の前提、中=実運用品質、相談=トレードオフ判断。

| ID | タスク | 優先 | 目標 | 備考 |
|----|--------|------|------|------|
| M005 | 未使用依存 `redis` を削除 | 高 | G1 | `redis` はどこも import していない（juice 抽出残骸）。~~httpx~~ は **http backend で使用するので残す**（当初は未使用だったが M018 で http backend を追加）。S3=aiobotocore / NATS=nats-py / HTTP=httpx / local=stdlib |
| M006 | LICENSE 追加 | 高 | G1 | OSS 配布の必須要件。現状ゼロ |
| M007 | `py.typed` 追加（PEP 561） | 高 | G1 | 型ヒントを書いているのに配布で効かない |
| M008 | PyPI メタデータ整備 | 高 | G1 | authors/license/readme/classifiers/urls/keywords。現状 name/version/description のみ |
| M009 | 統一例外階層 `ManystoreError`（+ NotFound 等） | 中 | G2 | stdlib 例外混在。backend が広い except で握りつぶす箇所（nats get/exists が任意 Exception を None 化）を整理 |
| M010 | local backend の非ブロッキング化 | 中 | G2 | `read_bytes`/`write` を async 内で同期実行＝event loop を塞ぐ。`asyncio.to_thread` でオフロード |
| M011 | 既定で安全（キー検証）/方針明確化 | 中 | G2 | 生 backend はキー検証なし＝`../escape` で脱出可。安全が `Safe*` opt-in の foot-gun |
| M012 | `list(prefix=...)` / pagination | 中 | G3 | 現状 limit のみ。prefix 絞り込み・継続トークンが無く大量キーで非効率 |
| M013 | メタデータ / content-type | 中 | G3 | S3・NATS はネイティブ対応だが共通 IF に無い |
| M014 | 操作レベル retry/timeout | 低 | G3 | 現状 connect のみ。put/get の一時失敗に未対応 |
| M015 | logging（操作・リトライの可視化） | 低 | G3 | 観測性なし |
| M016 | テスト拡充（エラーパス/並行/大容量） | 中 | G2 | fake は happy path 中心 |
| M017 | Python サポート範囲（3.10+ へ広げるか） | 相談 | G4 | `>=3.14` は採用障壁。広げるなら future import 復活＋ruff 設定。3.14純度 vs 採用のトレードオフ |
| M020 | UI 改善: パンくず階層ナビ + コピー/生パス編集 | 完了 | **ユーザー要望（2026-06-21）**。(1) パスを `dir1 / dir2 / dir3` のクリック可能パンくずに（各階層へジャンプ）。(2) 左にコピーボタン＋空きスペースクリックで生パス textbox 化（貼り付け）。KVS はフラットキーだが `/keys?prefix=` が prefix 配下の全キーを返す→フロントで `/` 区切りに畳んで仮想ツリー表示＝中間階層でも直下フォルダ/ファイルを列挙可能。実装は `static/` のみ（サーバ不変）|
| M021 | S3 ゲートウェイ（manystore を S3 API で公開）+ backend=s3 パススルー | **S1・S2 実装済 / S3・S4・繰延ページング・残未決=残タスク** | **supervisor interrupt（2026-06-23, normal）**。M019 P5 を独立タスク化。設計は `m021-s3-gateway-plan.md`。<br>**S1 完了（2026-06-23）**: 新サブパッケージ `manystore.gateway`（`__init__`/`app.create_gateway`/`routes`/`__main__`）＋ `manystore/implement/s3map.py`（delimiter 畳み込み + S3 XML 生成、HTTP 非依存）。GET/PUT/HEAD/DELETE/ListObjectsV2(prefix+delimiter) を `StorageService` 上に 1:1 実装。bucket=context、S3 XML は stdlib ElementTree（新依存ゼロ）、FastAPI 遅延 import・`[server]` extra 流用。コア IF 不変。SigV4 検証なし（Q2＝gateway 認証へ委譲）。例外→S3 エラー XML（NoSuchKey/NoSuchBucket/AccessDenied/InvalidArgument）。テスト: `tests/ui/test_gateway.py`(8) + `tests/ui/test_s3map.py`(5)。`make check` 緑（72 passed, 1 skipped）。<br>**S1 実クライアント往復検証（2026-06-23 後続サイクル）**: aiobotocore（コア依存・botocore 内包の**実 S3 クライアント**）を `endpoint_url=<起動 gateway>` に向け、**新依存ゼロ**で PUT→GET→HEAD→ListObjectsV2→DELETE を実往復検証。uvicorn を ephemeral port（実ソケット）で別スレッド起動。`tests/ui/test_gateway_s3client.py`(4)。**齟齬ゼロ**（ETag/ContentLength/XML 名前空間/エラー Code/ステータスを botocore がそのまま受理）。`make check` 緑（**76 passed, 1 skipped**）。<br>**S2 完了（2026-06-23）**: Multipart Upload を**コア IF 不変**で実装＝`manystore/gateway/multipart.py`（状態は**ストア上の予約キー空間** `.manystore-mpu/{uploadId}/{partNumber:05d}`＝サーバ再起動・複数プロセス耐性。`StorageService.put/get/delete/list_entries` だけで合成）＋ `s3map.py` に multipart XML 補助（initiate/complete render・complete parse）＋ `routes.py` で PUT/POST/DELETE を query 多重化分岐（`?uploads`=Create / `?partNumber&uploadId`=UploadPart / `?uploadId`+本文=Complete / DELETE+`?uploadId`=Abort）。Complete は parts を**リクエスト本文の指定順**で結合し**1 回の put（all-or-nothing）**→一時 part 掃除。ETag=`<concat-md5>-N`（S3 multipart 規約）。part 上書きは last-writer-wins、ListObjectsV2 は予約プレフィクスを除外。**ListParts/ListMultipartUploads は YAGNI 見送り**。テスト +11（s3map XML 4 + in-process route 5 + 実 aiobotocore 往復 2: Create→UploadPart×3→Complete→GET 一致 / Abort）。`make check` 緑（**87 passed, 1 skipped**）。<br>**残タスク**: S3 passthrough（`SupportsPresign` + redirect/proxy）/ S4 SeaweedFS **実機 backend** 疎通（実 client 往復は上記で前倒し済み＝残るは SeaweedFS 実機とパススルー）/ **繰延: ListObjectsV2 の continuation token ページング**（S1 は max-keys を既定上限 1000 でクランプ・打ち切りのみ＝Q3）/ **見送った multipart 補助 API**（ListParts / ListMultipartUploads＝YAGNI）。残未決: Q1 passthrough 既定値 / Q4 presigned TTL / Q5 直送時 validate_safe_path。Q6 extra 命名＝`[server]` 相乗りで確定 |
| M023 | S3 + native REST を**単一エントリポイントに統合** | **実装済（2026-06-23）** | **supervisor interrupt（2026-06-23, normal）**。S1/S2 で並存していた 2 アプリ（`gateway`=S3 互換 / `server`=native REST）を 1 つの FastAPI に束ねる。**方式=`include_router(prefix=...)`**（`mount()` は Starlette で lifespan が走らない落とし穴ゆえ却下）。`server/routes.py`・`gateway/routes.py` に `build_router(service)->APIRouter` を追加（既存 `@app.*` 本体は不変・`register_routes` は `include_router` する後方互換シムへ縮退）。新 `manystore/combined.py:create_combined_app(service)` が `/manystore`=native REST/WS・`/s3`=S3 ゲートウェイを include し、**共有 service を 1 回だけ connect/aclose する単一 lifespan**を持つ（二重 connect/aclose を回避）。起動 `python -m manystore --config <toml>`（`manystore/__main__.py`・既定 8000）。S3 クライアントは `endpoint_url=<host>/s3`（path-style）。**後方互換**: 単体 `create_app`/`create_gateway`・`python -m manystore.server`/`.gateway` は不変（静的 UI も `create_app` 側に残置）。統合アプリは `/` の StaticFiles を持ち込まない（prefix ルータと衝突＝スコープ外）。新規/変更: `combined.py`(新)・`__main__.py`(新)・`server/routes.py`・`gateway/routes.py`・`tests/ui/test_combined.py`(新+4)。コア IF 不変・新依存ゼロ。`make check` 緑（**91 passed, 1 skipped**／87→+4）。詳細 `m021-s3-gateway-plan.md`「統合エントリポイント化（M023）」 |
| M024 | 上りエスカレを pull 型へ＋スキル参照名を層エイリアスに統一 | **未着手（バックログ・priority low）** | interrupt（dotfiles supervisor, 2026-06-22）由来。(1) 親 interrupt への push をやめ自分の `.work/skills/flow-memory-bank/outbox/` に 1 件 1 ファイルで積む（worker は親を知らなくてよい＝supervisor が `workers_dir` 走査で回収）。activeContext 等の push 前提記述も pull 型へ更新。(2) スキル参照名を層エイリアス `[[flow]]`/`[[role]]`/`[[unit-quality]]` に統一。データ slot `.work/skills/memory-bank/`→`flow-memory-bank/` 移行は supervisor が `git mv` 済（残るは MB 文書内の旧名参照の追従）。priority low（UI/統合を止めない・次に MB を触る機会に） |
| M022 | ストレージ適合性テスト（conformance / contract test suite） | **フェーズ1（メソッド存在）＋フェーズ2（挙動契約）完了 / シグネチャ検査・実 backend 疎通は残** | **ユーザー要望（2026-06-23, 対話）で着手**。<br>**P1 メソッド存在**: `manystore/conformance.py`（`assert_key_value_store`/`assert_file_store`/`assert_implements`/`missing_members`/`required_members`＝`typing.get_protocol_members` で Protocol メンバを取り callable 属性として在るか）。ruff は型/Protocol 準拠を検査できない（linter/formatter）ので実行時 conformance で代替。<br>**P2 挙動契約（2026-06-23）**: `check_key_value_store_contract(store, *, writable=True)` / `check_file_store_contract(...)` を追加＝接続済みストアを実際に叩き backend 非依存の振る舞い（欠損 None / get_or_raise FileNotFoundError / get(default) / 上書き / list・iter 部分集合 / cp は src 残存 / mv は src 消失 / delete 冪等 / バイナリ・ネストキー安全 / IO ラウンドトリップ全体・部分 read）を検証。read-only は `writable=False`＝write 系が `io.UnsupportedOperation`。**既存 `test_e2e_backends.py` の `_crud_roundtrip` をこの契約に置換**（重複解消・実 backend で契約を注入）。`tests/test_conformance.py` で Dict/Local の KVS+FileStore・HTTP read-only を契約検証。`make check` 緑（**108 passed, 1 skipped**）。正本は `docs/architecture.md`。<br>**残**: シグネチャ検査／必須 vs optional 契約境界の精緻化（list 非対応 backend 等）／実 backend（SeaweedFS/実 NATS）での契約緑（`make e2e-up` で local は契約緑）。原指示 `interrupt/archive/.../storage-conformance-test-suite.md`（supervisor, normal）。優先 normal |
| M026 | **stream インターフェース（第3の族・新コア IF）** | **未着手・設計先行（相談）** | **ユーザー要望（2026-06-23, 対話）**。storage / kv の他に **stream**＝単一ターゲットに**接続を張り続けて入出力**するチャネル族。jsonl のような**追記**、NATS のような**トピックをファイルと見なす**。**基本はバイトを流す**ところから（MVP=byte stream）。3 族整理＝kv(バッファ・有限・get/put) / storage(ストリーム・**有限ファイル**・open_reader/writer) / stream(ストリーム・**無境界チャネル**・**append/follow(tail/subscribe)**)。核は FileStore で表現できない **tail/subscribe＋継続 append**＝**新コア IF（`StreamStore`・要設計）**。kv/json と違い facade では済まない＝projectbrief「最小・汎用/YAGNI」と緊張するので **doc-first で合意してから着手**。MVP の上に jsonl レコード境界 / NATS トピックを backend/エンコード特化で重ねる。HTTP 公開は将来 `stream/*` 族（WS/chunked/SSE）。未決: IF 名・形・方向／有限 vs 無境界・replay 可否／backend(local 追記+tail・NATS subject pub/sub から・S3 は append 非対応で対象外か)／connect 整合／FileStore と別 IF にする理由の明文化。詳細 `interrupt/archive/2026-06-23-stream-interface.md` |
| M027 | **Local の KV を FileStore から派生**（`KeyValueFromFileStore`・メイン実装を `LocalFileStore` に集約） | **実装完了（2026-06-23）** | **ユーザー要望（2026-06-23, 対話）**。設計の壁（FileStore Protocol に list/exists/delete が無い）を対話で **選択肢(b)＋汎用アダプタ＋Protocol 拡張**に確定して実装。<br>**完了内容**: (1) `LocalFileStore` を真実の実装に＝put/get・iter/list/exists/delete・vacuum・cp/mv を filesystem-native に集約（旧 `LocalKeyValueStore` から移設、二重持ち解消）。get は自身の open_reader を流用。(2) `LocalKeyValueStore` を `class LocalKeyValueStore(KeyValueFromFileStore)`＝`KeyValueFromFileStore(LocalFileStore(dir))` の薄いビューに（get/put は open_reader/open_writer 越し、iter 等は素通し委譲、vacuum のみ Local 固有 override）。(3) `FileStore` Protocol を iter/list/exists/delete/cp/mv/connect/aclose で拡張し `KeyValueFromFileStore` の `# type: ignore` 全廃。`KeyValueFromFileStore` を `manystore.kv` 公開。<br>変更: `async_storage.py`・`backends/local.py`（全面再構成）・`kv.py`・`tests/test_storage.py`(+2)。`make check` 緑（**93 passed, 1 skipped**）。背景方針は systemPatterns 原則6・M025/M026 と連動 |
| M027b | **FileStore = KeyValueStore + IO の波及整理**（残: Safe FileStore・Sync 側） | **S3/NATS/HTTP 完了 / Safe・Sync 残** | M027 の follow-up。**Protocol を `FileStore(KeyValueStore)` = KVS + open_reader/open_writer に確定**（ユーザー提案）。各 FileStore は KVS 面（put/get/get_or_raise・iter/list/exists/delete/cp/mv・connect/aclose）まで満たすべき。<br>**S3/NATS 完了（2026-06-23・ユーザー要望）＝「寄り」で核を配置**: S3 は **file 寄り**（streaming が強み）→ `S3FileStore(S3KeyValueStore)` で KVS 核を継承し open_reader/open_writer を **native streaming**（range body / multipart）で実装。NATS は **kv 寄り**（whole get/put が native・真の streaming は nats-py のスレッド安全性で deferred）→ `NatsFileStore(NatsObjectKeyValueStore)` で KVS 核を継承し IO は **whole の上に buffer 合成**（共有 `_KvReadFileObject`/`_KvWriteFileObject` を流用＝専用 `_NatsBufferedWriter` を削除）。両者とも `make check` の fake テストで KVS 面（put/get/get_or_raise/iter/exists）を検証。`LocalFileStore` は M027 で完全準拠済。<br>**HTTP 完了（2026-06-23・ユーザー要望/対話）**: `HttpFileStore(HttpKeyValueStore)`＝kv 寄り・read-only。KVS 面（get/get_or_raise/exists・write 系は `io.UnsupportedOperation`）を継承し、open_reader は whole get の buffer 合成（`_KvReadFileObject` で get_or_raise 再利用＝旧 open_reader の GET 重複を解消）。**read-only は実行時 `UnsupportedOperation` 許容で確定**（Protocol で静的に表さない＝capability 分割はしない・YAGNI。必要なら M011 と別タスク）。test +1（read IO＋KVS 面＋write 系が UnsupportedOperation）。`make check` 緑（**99 passed, 1 skipped**）。<br>**残**: `SafeFileStore`（FileStore ラッパ＝KVS 面も検証付きで委譲。方向は `SafeFileStore(SafeKeyValueStore)`＋検証付き open_reader/open_writer だが `_store` の型／systemPatterns の「SafeKeyValueStore が download も担う」陳腐化記述の整理が要る＝別サイクル）／`SyncFileStore`(Protocol を `SyncFileStore(SyncKeyValueStore)` に鏡映＋`AsyncToSyncFileStore` ブリッジが未実装＝FileObject 境界の同期化が要る・優先度低)。需要が出たら着手 |
| M027c | **get_or_raise/get(default) を client/service にも波及** | **未着手（follow-up・低）** | **ユーザー要望（2026-06-23, 対話）で KVS の get を再設計**＝get_or_raise を primitive にし `get(key, default=None)` を共有基底 `KeyValueStoreBase` で実装（欠損は `FileNotFoundError` に正規化）。適用済: Local(アダプタ)/S3/NATS/HTTP/Safe/Array/DownloadCache/sync ブリッジ＋ Protocol。<br>**残**: `RemoteKeyValueStore`(client/remote.py)・`implement/service.py`（HTTP 越し KVS）は今回スコープ外で get_or_raise 未実装＝`KeyValueStore` Protocol を部分準拠。REST/WS の get に default/raise を表に出すか（404 と空値の区別など）含めて整理。需要が出たら着手 |
| M025 | 名前空間を buffer 性で再編（`kv`=buffered / `storage`=streaming） | **フェーズ1（移設）完了 / フェーズ2・3 残** | **ユーザー要望（2026-06-23, 対話）**。設計 `m025-namespace-restructure-plan.md`。第1階層を buffer 性で分ける＝`kv/*`(バッファ・辞書的) / `storage/*`(ストリーミング・ファイルオープン的)。S3 は意味的に kv だがラージ＋multipart で storage 側。4 ルート: `kv/raw`(既存=native REST 生バイト) / `kv/json`(新規=JSON 検証 facade) / `storage/s3`(既存=S3 GW) / `storage/manystore`(新規=FileStore streaming over HTTP)。全て server facade 層＝**コア IF 不変**。<br>**フェーズ1 移設 完了（2026-06-23）**: combined アプリの prefix を `/manystore`→`/kv/raw`・`/s3`→`/storage/s3` に付け替え（`combined.py`/`__main__.py` docstring・`tests/ui/test_combined.py` URL）。native 内部パス（`/contexts/.../objects/...`）は不変＝prefix 付け替えのみ。S3 クライアントは `endpoint_url=<host>/storage/s3`。後方互換エイリアスは張らずクリーン移設（M023 未リリース）。standalone（`create_app`/`create_gateway`・`python -m manystore.server`/`.gateway`）は不変。新依存ゼロ・テスト数不変。`make check` 緑（**91 passed, 1 skipped**）。<br>**残**: フェーズ2 `kv/json`（PUT で json 検証→不正 400 / GET は必ず `application/json`・保存方式=素通し vs 正規化 未決）／フェーズ3 `storage/manystore`（FileStore ストリーミング HTTP 公開＝一番重い新規・range/chunked 設計要）／README・examples の起動例パス追従確認 |
| M019 | ストレージの UI | **P1〜P3 完了** | — | **ユーザー要望（2026-06-21）実装済み**（詳細 `m019-ui-plan.md`）。`manystore.{implement,server,client}` 一体型＋`[server]` extra。汎用 CRUD UI＋WS ライブ通知＋featured 重点設定。残: P4(http_store RW拡張)・P5(S3 gateway)・LocalWatcher(inotify)・認証。以下は旧スコープメモ→精緻化された要件: (1) manystore IF の上に公開し、その protocol にフロントエンドが接続。(2) **複数コンテキスト（`.work` など）の「ディレクトリ公開」**。(3) ディレクトリを **監視し WebSocket でライブ通知**（更新push）。(4) UI→サーバへ **更新依頼（書き込み）** 可。(5) **本質: interrupt 受信箱へ作業テキストを投入**（remote drop-to-interrupt）。(6) 二次目標: **汎用ストレージ UI**。設計の要点: 既存 read-only `http_store` の対になる **manystore-server（HTTP+WS）** を新設し、その REST/WS が「IF の上に公開する protocol」になる（http_store を RW 拡張すれば client にもなる）。S3 gateway 案は汎用 UI には効くが **watch/notify/interrupt は S3 protocol に無く既定の S3 browser では不可**＝核心要件を満たせない。詳細計画は activeContext 参照 |

**ゴール（段階）**: G1=配布できる（M005〜M008）→ G2=安心して使える（M009〜M011・M016）→
G3=機能十分（M012〜M015）→ G4=広く使える（M017 判断）。

> **M019（UI）はスコープ判断が要る**: manystore 本体は「ストレージ抽象ライブラリ」。UI を本体に入れると
> スコープが膨らむため、別パッケージ（例 `manystore-ui`）か別リポが妥当か、着手前にユーザーと合意する。

## 現状ステータス

独立ライブラリ化は完了し M001〜M004 完了（実 backend 疎通 / CI / README / 3.14 化）。**評価により次フェーズの
改善バックログ M005〜M017 を洗い出し**（上記）。直近は配布前提の G1（未使用依存削除・LICENSE・py.typed・メタ）
が安く効く。M002 は NATS / S3(path) を実機 E2E で検証済み。

## 既知の問題

- ~~S3 の実機検証は保留~~（M002 で解消。`make e2e-up` が SeaweedFS に dev identity を登録し s3-path 実証）。
- `s3-virtual`（ドメインスタイル）はローカル S3互換では `bucket.<host>` を名前解決できず常に skip。これは
  **virtual-host の仕様上の制約**（実 AWS 等の DNS 環境向け）であり未解決バグではない。
- ~~ルート README が無い~~（M004 で解消）。~~CI 未設定~~（M003 で解消）。

## 意思決定の変遷

- ストレージ抽象は独立ライブラリとして自己完結させる。利用側固有の結線は利用側の adapter に閉じ、
  manystore 本体は最小・汎用に保つ（IF を利用側都合で拡張しない）。
- **S3 アドレッシングスタイルを明示パラメータ化（M002 で発見）**: 既定の virtual-host だと S3 互換サーバ
  （minio/SeaweedFS）で `bucket.<host>` を名前解決できず接続不可。fake テストでは気づけず実機 E2E で露見。
  方針は「**既定 virtual（ドメイン）、利用側が `"path"` を opt-in**」。`S3*Store(addressing_style="virtual")`、
  `create_key_value_store(s3_addressing_style=...)`、`connect_key_value_store("s3", s3_addressing_style="path")`。
  実 AWS は既定 virtual のまま。
- **E2E テストはパラメタライズ**（`tests/test_e2e_backends.py`）: 同一 CRUD を local / nats / s3-virtual /
  s3-path に注入して回す（実行する test は1つ、注入インスタンスだけ違う）。各ケースは未到達/認証未整備なら skip。
- **Python 3.14+ を前提に確定（M003）**: 3.14 は注釈遅延評価（PEP 649）が既定なので、自クラス等を戻り値
  注釈に使う前方参照はそのまま valid＝`from __future__ import annotations` は不要。当初 `requires-python>=3.10`
  だったため ruff が forward-ref を F821 と判定し future import を入れたが、方針は「3.14+ 前提」なので撤回。
  `requires-python = ">=3.14"` ＋ ruff `target-version = "py314"` にし、future import を全廃。ただし ruff は
  **py314 対応版が必須**（0.9.1 は py314 未対応）→ `RUFF_VERSION` を **0.15.18** に更新。
- Memory Bank: 独自 2 ファイル構成 → **Cline 準拠 6 ファイル**へ移行。作業フォルダは `.cache/` 案 →
  `.work/skills/memory-bank/` に確定（`.cache/` は「捨てる」含意のため不可。`.work/` は commit する状態）。
