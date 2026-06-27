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
- **prefix は optional capability**（`SupportsPrefixListing`）＝S3 native／他は `scan_prefix` で明示 opt-in。
  非対応は **fail-loud**（`NotImplementedError`・暗黙フォールバック無し）。Safe/Array が委譲伝播。
- async / sync ブリッジ（`AsyncToSyncKeyValueStore`）、接続ライフサイクル（`connect_key_value_store`/`connecting`/
  `ConnectPolicy`）、安全パス（`validate_safe_path`/`SafeKeyValueStore`/`SafeFileStore`）、合成（`ArrayKeyValueStore`/
  `DownloadCache`）。
- **適合性ツール `manystore.tools.conformancer`**: メソッド存在チェック＋`FileStoreTester`（DictFileStore をオラクルに
  差分検証。`run_light` 実装済＝12 観点・副作用も記録・`save_report` で JSON 保存）。
- **UI/サーバ（`manystore[server]` extra）**: `serving.services`/`serving.server`/`client` の 3 層。任意 context を HTTP+WS で公開する
  汎用 CRUD UI。context = ArrayStorage 第一階層（bucket）。native REST は `{bucket}/{path}`、WS ライブ通知、
  `views.featured` で重点パス。`RemoteKeyValueStore` でサーバ越し KVS。
- **S3 互換ゲートウェイ `manystore.serving.gateway`**: GET/PUT/HEAD/DELETE/ListObjectsV2 + Multipart を `StorageService` 上に
  1:1 合成（コア IF 不変・実 aiobotocore 往復で検証済）。
- **統合エントリポイント `manystore.combined`（`python -m manystore`）**: native REST/WS（`/kv/raw`・buffered）と
  S3 ゲートウェイ（`/storage/s3`・streaming）を単一 lifespan で束ねる。
- **CI**: GitHub Actions で `make check`（`ci.yml`）。**テスト軽重分離**＝`make test`（fast・`-m "not slow"`）/`make test-all`（全部）。
- **Docs サイト（GitHub Pages・2026-06-25）**: `pages.yml` で MkDocs Material をビルド＝`make docs`（先に `make conformance-docs`
  で spec 再生成 → `mkdocs build --strict`）。PR はビルド検証のみ・**main push のみ公式 Actions（upload-pages-artifact/
  deploy-pages）で実公開**。`index.md` は snippets で README を取り込む単一ソース。docs 依存は `docs` group（mkdocs-material）。
  **要手動: Settings → Pages → Source = GitHub Actions を有効化**（初回のみ・ユーザー作業）。
  直近 fast = **120 passed, 12 deselected**。実 backend E2E（NATS / S3 path-style）検証済（`make e2e-up`）。
- **local backend は非ブロッキング**（M010）: 全 IO syscall を anyio でスレッドへオフロード＝event loop を塞がない。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

| ID | タスク | 優先 | 備考 |
|----|--------|------|------|
| M046残 | conditional put の残（NATS CAS / S3 GW If-Match） | low | **MVP＋native REST/remote 配線 完了（2026-06-28）＝完了マイルストーン参照**。残＝① NATS の revision/digest CAS（`put(if_match)` は現状 loud `NotImplementedError`・head は digest を etag に・要 nats-py API 調査＋実 NATS で conformance）② S3 ゲートウェイ（S3 互換 XML 面）への If-Match/If-None-Match 配線（native REST は配線済）。**native REST＋remote の条件 put は配線済**＝RemoteKeyValueStore 経由で create/update CAS を HTTP 越し conformance で機械検証。設計 `plans/m046-conditional-put-plan.md` |
| M051 | kubernetes backend（M050 の具体 sink） | 相談 | ユーザー要望（2026-06-27・討議中・doc-first）＝put=server-side apply / get=補完済み live（put≠get）。キー=`namespace/resource_type/name`（`.yml` 含めず・group/version は discovery 補完・衝突時 `type.group`）。**FileInfo に世代情報**（resourceVersion/generation/uid/creationTimestamp）。**resourceVersion CAS を M046 の参照実装**に（`if_version` 不一致は ConflictError）。ローカル側=`KubeManifestStore` ラッパ（パス↔内容の同一性検証＝Safe 風 1 枚）。依存=`kubernetes-asyncio` を extra `[k8s]`（遅延 import）。cluster-scoped は後回し。詳細は interrupt |
| M012 | `list(prefix=...)` / pagination | 中 | prefix は core `iter_all(prefix=…)` 引数化済（M030 capability は 2026-06-26 廃止）。**pagination 未対応**。設計案（2026-06-26 対話・要 doc-first）＝(a) `iter_all`/`list_all` に **offset+limit** を足す（単純・全 backend で scan 可だが大 offset は O(n)）／(b) **cursor/continuation-token** 形式（S3 ContinuationToken・NATS 等の native と整合・M021 の continuation と同一機構）。加えて **返り値を range メタ付きの独自型**にする案＝iter は「何件目〜何件目」を、list は from/to 件数属性を持つ（pagination メタ＝**file/value パラダイム内**。却下した transport の request/response 封筒とは別物）。未確定＝offset/limit vs cursor の二択と、独自結果型を入れるか。M021（S3 GW continuation）・M044（limit 既定の定数化）と連動 |
| M013 | メタデータ / content-type | 中 | S3・NATS は native 対応だが共通 IF に無い |
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
| M044 | spec/既定値の定数を集約 | low | マジックナンバー・既定値を **共通知識の名前付き定数**として 1 か所に正本化し散在を断つ。種＝HTTP list cap `10_000`（`client/remote.py`）／list 既定 `limit=1000`（client `list_entries`・service・conformancer `args.get("limit",1000)`）等。`DEFAULT_CACHE_DIR` と同流儀の既定値置き場（定数モジュール）を設け、spec・デフォルト値に関わる値は名前で参照する。inline `# TODO(M044)` で sweep 可 |
| M040 | ロードバランサーストレージ層 本実装 | 相談 | scaffold 配置済（`surfaces/loadbalancer.py`・本体 NotImplementedError・**facade 未公開**）。**負荷メトリクスで適切な1 backend を選ぶ**動的プレースメント（シャーディング/レプリケーションではない）。ネタ＝capability `SupportsLoadStats`/`LoadStats`＋`BalancePolicy`（RoundRobin/MostFreeSpace/LeastLoaded）。Array の兄弟。**未解決＝読みルーティング**（probe-all 既定 vs 配置インデックス）。local の free は `shutil.disk_usage`、cpu/mem は別途エージェント/エンドポイント要 |
| M045 | `put2` ＝ error-as-value（Go 風 `(Error\|None, FileInfo)`） | 相談 | 別メソッド `put2(key, value) -> tuple[Error \| None, FileInfo]`＝成功は `(None, FileInfo)`／失敗は `(Error(...), FileInfo?)` で **エラー側に任意情報を載せられる**（“**半分** request/response 型”＝成功は FileInfo のまま・封筒は被せない。却下した full envelope とは別物）。要 doc-first。**未確定**＝(1) 例外ベース fail-loud（既存 put が raise）との二重化＝どの op が raise／どの op が tuple か、混在の指針／(2) `Error` 型の定義（共通基底 or 既存例外 `Exception\|None`／backend 固有情報の持たせ方）／(3) core IF に載せるか別系統 method か（載せるなら async↔sync lockstep ＋ conformancer parity ＝M043 前提）／(4) get/delete 等への波及。put→FileInfo（済）と request/response 封筒却下（projectbrief 非ターゲット）の中間地点 |

> **ゴール段階**: G1=配布できる（M005〜M008 完了）→ G2=安心して使える（M009〜M011・M016）→
> G3=機能十分（M012〜M015）→ G4=広く使える（M017 判断）。

### 完了マイルストーン（要点のみ・経緯は git 履歴）

- **M046 MVP（2026-06-28・完了）**: put の並行安全性（lost-update 検出）を **conditional put** で実装。
  **派生メソッドを作らず put 1 本＋任意 `if_match`**（ユーザー確定）＝`put(key, value, *, if_match=None)`:
  None=原子＋直列化の **LWW**（失敗しない）／`ABSENT`=create-only（既存なら `ConflictError`）／
  `FileInfo`=update CAS（etag 不一致は `ConflictError`）。**opaque version 文字列は出さず**、比較トークンは
  `FileInfo` 内に畳む（`_Absent`/`ABSENT` センチネル＋`type IfMatch` を protocols に新設・kv facade で公開）。
  **`head(key)->FileInfo` 新設**（`FileInfo` に `modified_at`/`etag` を `NotRequired` 追加・既定実装は
  get 由来で etag=None・native backend が override）。backend: **dict=メタストア**（通し番号 `_seq` を etag に・
  ABA 安全）／**local=os.link（create）＋flock+stat 比較+replace（update CAS）・head は os.stat**／S3=
  IfNoneMatch/IfMatch＋HeadObject／NATS=head のみ（CAS は P2 で loud）／http=read-only（head は HTTP HEAD）。
  ラッパ（Safe/Array/DownloadCache/sync_bridge/KeyValueFile(From)Store）は put if_match＋head を委譲。
  M043 lockstep を全揃え（Protocol async/sync・`_StoreBase`・parity 緑）。**conformancer が並行安全性を強制**＝
  `assert_put_if_absent_concurrency_safe`（create 競合・put(if_match=ABSENT)）＋新 `assert_put_if_match_
  concurrency_safe`（update CAS）。テストは **実ストア経由**（Dict/Local）＝旧 `_ConditionalDict` フェイクを
  廃し、否定テストだけ故意 TOCTOU の `_RacyCreateDict` を残置（チェッカの牙を担保）。put 戻りは安価な
  `{filename,size}` 据置。残＝M046残（NATS CAS / serving 配線 / remote）。fast 139 passed・`make check` 緑。
- **M046残 remote 署名検証（2026-06-28・案B step1・ユーザー対話）**: 「HTTP 越し conformance」の前段＝
  `RemoteKeyValueStore` が `AsyncKeyValueStore` を**署名レベル**で満たすかの機械ガードを追加。conformancer に
  再利用ヘルパ `concrete_store_signature_errors`/`assert_concrete_store_signatures` 新設＝**メンバ存在＋
  パラメータ署名の一致**を見て **戻り注釈の narrowing は許容**（`iter_all` が Protocol の `AsyncIterable`→
  部分型 `AsyncIterator`＝全 9 backend/surface 共通の house convention・LSP 安全な共変）。strict な
  `base_protocol_parity_errors` は concrete store でこの narrowing を誤検出＝**base↔Protocol lockstep 専用**と
  棲み分け（concrete store 用の新ヘルパが正本）。remote は put の `if_match`・`head`・`create` まで param drift
  ゼロを確認。test=`test_remote_kvs_signature_parity`＋ヘルパ健全性 `test_concrete_store_signatures_tolerate_return_narrowing`
  （narrowing 許容／param drift 検出の二面）。**スコープ確定＝今回は署名検証のみ**（CAS の HTTP 越し
  conformance は serving 配線が前提＝M046残に保持）。`make check` 緑（142）。
- **M046残 serving 配線＋remote 条件 put（2026-06-28・案B step2/3・ユーザー指示で実装）**: conditional put を
  **native REST と remote クライアントに end-to-end 配線**し、**CAS の並行安全性を HTTP 越し conformance で機械検証**。
  - **serving/server/routes.py**: PUT が条件ヘッダを `if_match` に解く＝`If-None-Match: *`→`FileInfo.absent()`
    （create-only）／`If-Match: "<etag>"`→`FileInfo(etag=...)`（update CAS）／無し→None（LWW）。HEAD が
    メタを露出＝`ETag`（CAS トークン quote）＋独自 `X-Manystore-Size`/`X-Manystore-Modified-At`（標準 HTTP-date は
    秒精度で lossy）。条件不一致は backend の `ConflictError`→既存 `_on_error`/`to_problem` が problem(409)。
  - **serving/services/service.py**: `head`/`head_or_absent` 新設（合成キーを bare key へ・etag 透過）＋
    `put(..., if_match=None)` に拡張（`_array` へ委譲）。
  - **client/remote.py**: `RemoteKeyValueStore.put` が if_match を条件ヘッダへ写し、`head` を override（HEAD の
    ETag/size/modified_at から version 付き FileInfo を組む＝既定 head は get で etag=None ゆえ CAS 不可）。
    `ManystoreClient.put` は 409→`ConflictError` に戻す＋`head_meta` 追加。
  - **tests/ui/test_client.py +4**: HEAD の version 露出／単発の create-only・update CAS の Conflict／
    **conformancer の `assert_put_if_absent/if_match_concurrency_safe` を HTTP 越し**（client→server→local）に回す。
  - 残＝M046残（① NATS revision CAS〔要 nats-py 調査＋実 NATS〕② S3 GW の If-Match）。`make check` 緑（146）。
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
prefix capability（fail-loud）」で安定。protocols.py が契約＋既定実装の単一源泉。次は G2（安心して使える＝
error-swallow 監査 M036 / Safe 既定化 M011・M032 / テスト拡充）と UI/GW の残フェーズ。

## 既知の問題

- `s3-virtual`（ドメインスタイル）はローカル S3 互換では `bucket.<host>` を名前解決できず常に skip。
  **virtual-host の仕様上の制約**（実 AWS 等の DNS 環境向け）であり未解決バグではない。
- `make test`（fast）は lint を回さない＝format ドリフト（特に CJK 行の E501）は `make format` でしか出ない。

## 意思決定の変遷

- **atomic write は torn-write 防止であり排他制御ではない／並行更新は conditional put で別途**（2026-06-27・
  ユーザー指摘を受けた方針メモ・未実装＝M046）: local の temp+`os.replace` は「壊れた半端ファイルを見せない」
  原子性のみを保証し、同一キーへの並行 put の**lost update は検出しない**（last-writer-wins）。検出は
  **version/etag の compare-and-swap** を **opt-in の conditional put として fail-loud に raise**。put 既定の
  無条件 set 契約は維持（最小-core）。**version は backend native を opaque な `version:str` に畳む**＝
  S3=ETag・NATS=revision・**local=mtime(+size)**。当初「mtime は不適」としたが**訂正**＝modern FS は ns 精度で
  etag 的に使える（ユーザー合意）。**真の難所はトークン選択でなく CAS の原子性**＝stat→比較→replace は TOCTOU で
  racy ゆえ commit をロック/原子 rename（`renameat2(RENAME_NOREPLACE)` 等）で直列化する必要。doc-first で M046。
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
