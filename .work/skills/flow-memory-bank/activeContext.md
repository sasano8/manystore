# Active Context

## 現在のフォーカス

**M027 Local KV を FileStore から派生（実装完了・2026-06-23 後続・ユーザー要望/対話で設計確定）。**
M027 の「設計の壁」（FileStore Protocol に list/exists/delete が無く get/put しか派生できない）を、
ユーザーとの対話で **選択肢(b)＋汎用アダプタ＋Protocol 拡張** に確定し実装した。

- **真実の実装を `LocalFileStore` に集約**: put/get・iter/list/exists/delete・vacuum・cp/mv を
  filesystem-native に LocalFileStore へ移設（旧 `LocalKeyValueStore` から）。get は自身の
  `open_reader` を流用してストリーム入出力の上に全体取得を表現。
- **`LocalKeyValueStore` は薄いビュー**: `class LocalKeyValueStore(KeyValueFromFileStore)` で
  `KeyValueFromFileStore(LocalFileStore(dir))` を構成。get/put は下層 open_reader/open_writer 越し、
  iter/list/exists/delete/cp/mv は素通し委譲。vacuum だけ Local 固有として override（concrete 参照 `_fs`）。
- **`FileStore` Protocol を拡張**（iter/list/exists/delete/cp/mv/connect/aclose を追加）＝
  `KeyValueFromFileStore` の `# type: ignore[attr-defined]` を全廃。`KeyValueFromFileStore` を
  `manystore.kv` から公開（KeyValueFileStore の逆向き）。
- **テスト +2**（`test_storage.py`）: 汎用アダプタの直接往復（get/put/iter/exists/cp/mv/delete・欠損=None）／
  LocalKeyValueStore が KeyValueFromFileStore の薄いビューであること（KVS put→FileStore open_reader で同一読取）。
  既存の Local KVS テスト群は委譲経路をそのまま検証。`make check` 緑（**93 passed, 1 skipped**）。
- **follow-up（取りこぼし防止）**: FileStore Protocol 拡張で `S3FileStore`/`NatsFileStore`/`HttpFileStore`/
  `SafeFileStore`/`SyncFileStore` が新メソッド未実装＝**Protocol を部分的にしか満たさない**（CI に型チェッカが
  無く破綻なし・呼べば AttributeError）。これらに名前空間操作を備える（または narrow Protocol に整理する）のは
  別タスク＝**progress M027 follow-up** に記載。

**（前サイクル）M025 名前空間再編・フェーズ1（移設）完了（2026-06-23・ユーザー要望/対話）。** 統合アプリの HTTP 第1階層を
**バッファリング性**で再編する設計をユーザーと確定（設計 `m025-namespace-restructure-plan.md`）。軸＝`kv/*`(バッファ・
辞書的・全体 get/put) / `storage/*`(ストリーミング・ファイルオープン的・ラージ)。S3 は意味的に kv（key→bytes）だが
ラージ＋multipart で**ファイルオープン寄り**ゆえ storage 側。4 ルート＝`kv/raw`(既存=native 生バイト) / `kv/json`(新規=
JSON 検証 facade) / `storage/s3`(既存=S3 GW) / `storage/manystore`(新規=FileStore streaming over HTTP)。全て server
facade 層に閉じる＝**コア IF 不変**。

- **フェーズ1 = 移設（既存 2 ルートの再配置）を実装**。combined アプリの `include_router` prefix を
  `/manystore`→`/kv/raw`・`/s3`→`/storage/s3` に付け替えただけ（`combined.py`/`__main__.py` docstring・
  `tests/ui/test_combined.py` の URL・S3 `endpoint_url=<host>/storage/s3`）。native 内部パス（`/contexts/.../objects/...`）
  は不変＝prefix 付け替えのみ（最小・可逆）。kv が深く s3 がフラットなのは native がリッチなプロトコル（contexts/keys/events）
  だから（無理に平坦化しない）。
- **後方互換エイリアスは張らずクリーン移設**（M023 は昨日入ったばかりで未リリース＝外部利用者ゼロ）。standalone
  （`create_app`/`create_gateway`・`python -m manystore.server`/`.gateway`）は不変＝旧パスが要る人はそちら。
- **コア IF 不変・新依存ゼロ・テスト数不変**。`make check` 緑（**91 passed, 1 skipped**）。
- **残**: フェーズ2 `kv/json`（PUT で json 検証→不正 400 / GET は必ず `application/json`・保存方式=素通し vs 正規化 未決）／
  フェーズ3 `storage/manystore`（FileStore ストリーミング HTTP 公開＝一番重い新規・range/chunked 設計要）／
  README・examples の起動例パス追従確認。

## （旧フォーカス）

**M023 統合エントリポイント化 実装完了（2026-06-23・supervisor 指示）。** S1/S2 で並存していた 2 アプリ
（`gateway`=S3 互換 / `server`=manystore native REST）を**単一 FastAPI に束ね**、パス第一階層で
`/s3`（S3 互換）と `/manystore`（native REST/WS）に分けた。スコープは統合＋テスト追従のみ
（S3 passthrough / S4 実機は対象外）。コア IF（KeyValueStore/FileStore/StorageService）不変・新依存ゼロ。

- **採用方式 = `include_router(router, prefix=...)`**。`app.mount()` は Starlette でサブアプリの lifespan が
  走らず共有 service の connect が起動時に呼ばれない落とし穴ゆえ**却下**。
- **routes 層を APIRouter 化（小リファクタ）**: `server/routes.py` / `gateway/routes.py` に
  `build_router(service) -> APIRouter` を追加（`@app.*` デコレータ本体は不変＝`app` 変数が APIRouter を指すだけ）。
  `register_routes(app, service)` は `app.include_router(build_router(service))` の**後方互換シム**に縮退。
- **統合アプリ** `manystore/combined.py:create_combined_app(service)`: `/manystore` に native REST/WS、`/s3` に
  S3 ゲートウェイを include。**共有 service を 1 回だけ connect/aclose する単一 lifespan**（router は lifespan を
  持たないので二重 connect/aclose を回避）。S3 クライアントは `endpoint_url=<host>/s3`（path-style）。
- **新エントリ起動** `python -m manystore --config <toml>`（`manystore/__main__.py`・既定 8000）。
- **後方互換**: 単体 `create_app`/`create_gateway`・`python -m manystore.server`/`.gateway` は不変。静的 UI
  （`/` の StaticFiles）は従来どおり `create_app` 側に残し、統合アプリには持ち込まない（prefix ルータと root が
  衝突＝スコープ外）。
- **追加テスト**: `tests/ui/test_combined.py`(+4) = `/manystore` REST 疎通 / native PUT→`/s3` GET の service 共有 /
  実 aiobotocore で `/s3` 越し PUT/GET/HEAD/List/DELETE / multipart 往復（uvicorn を ephemeral port で別スレッド起動）。
- **検証**: `make check` 緑（**91 passed, 1 skipped**・S2 の 87 から +4）。format-check / lint clean。
- **残課題**: S3 passthrough（`SupportsPresign` + redirect/proxy）／S4 SeaweedFS 実機 backend 疎通／繰延ページング。

## （旧フォーカス）

**M021 S2（Multipart Upload）実装完了（2026-06-23・supervisor 指示）。** スコープは S2 のみ
（S3 passthrough / S4 実機は対象外）。S3 の multipart API を**コア IF 不変**で `StorageService` の上に薄く合成した。

- **新規/変更ファイル**: `manystore/gateway/multipart.py`（新・状態保持と create/upload/complete/abort のオーケストレーション）／
  `manystore/implement/s3map.py`（multipart XML 補助 = `render_initiate_multipart` / `render_complete_multipart` /
  `parse_complete_multipart` を追加・HTTP 非依存）／`manystore/gateway/routes.py`（PUT/POST/DELETE を query で多重化分岐・
  ListObjectsV2 から予約プレフィクス除外）／`manystore/gateway/__init__.py`（docstring）。
- **実装した API**: CreateMultipartUpload（`POST /{bucket}/{key}?uploads`・uploadId=uuid4 hex）／UploadPart
  （`PUT /{bucket}/{key}?partNumber=N&uploadId=X`・一時キーへ put・part の MD5 を ETag）／CompleteMultipartUpload
  （`POST /{bucket}/{key}?uploadId=X`＋本文 Part 列・**指定順**結合→**1 回の put（all-or-nothing）**→一時 part 掃除・
  ETag=`<concat-md5>-N`）／AbortMultipartUpload（`DELETE /{bucket}/{key}?uploadId=X`・冪等）。**ListParts /
  ListMultipartUploads は YAGNI で見送り**（progress バックログ）。
- **状態の保持方式 = ストア上の予約キー空間** `.manystore-mpu/{uploadId}/{partNumber:05d}`（インメモリ辞書ではない）。
  理由: **サーバ再起動耐性・複数プロセス/ワーカ耐性**（落ちても part が残り、どのワーカでも同じストアに積む）。予約
  プレフィクスは `validate_safe_path` を通る安全キー。ListObjectsV2 は予約プレフィクスを **gateway 側で除外**（service 不変）。
- **並行/上書き/順序**: 同一 (uploadId, partNumber) への再 UploadPart は **last-writer-wins**（put 自体がアトミック＝
  半端に混ざらない）。結合順は **Complete リクエスト本文の partNumber 順**を尊重（クライアント責務・サーバは再ソートしない）。
- **コア IF 変更 = なし**（`KeyValueStore` / `FileStore` / `StorageService` 公開 API いずれも不変）。新依存ゼロ
  （aiobotocore=コア依存・uvicorn=既存）。
- **追加テスト**: `tests/ui/test_gateway_s3client.py` +2（`test_real_s3_client_multipart_roundtrip`=Create→UploadPart×3
  （1MiB+0.5MiB+端数）→Complete→GET 結合一致＋ETag 末尾 `-3`＋一覧に予約キー非露出／
  `test_real_s3_client_multipart_abort_discards_parts`=Abort 後に本オブジェクト未作成＝NoSuchKey＋一時 part 掃除）。
  in-process route 分岐は `test_gateway.py` +5（Create/UploadPart/Complete/GET 一致・Abort・NoSuchUpload・partNumber 0=
  InvalidArgument・unknown bucket=NoSuchBucket）。XML 純ロジックは `test_s3map.py` +4。
- **検証**: `make check` 緑（**87 passed, 1 skipped**・S1 の 76 から +11）。format-check / lint clean。
- **残課題**: S3 passthrough（`SupportsPresign` + redirect/proxy）／S4 SeaweedFS 実機 backend 疎通／繰延ページング／
  見送った multipart 補助 API（ListParts / ListMultipartUploads）。

## （旧フォーカス）

**M021 S1 を「実 S3 クライアント往復」で検証補強（2026-06-23 後続サイクル・supervisor 指示）。**
S1 の既存テストは gateway 生成の S3 XML を stdlib ElementTree でパースするだけで**実 S3 クライアント往復が無かった**。
直前実装者は「実 client = 同期 boto3 は新依存」として S4 へ繰越したが、**manystore はコア依存に `aiobotocore>=2.0.0`
（botocore 内包の実 S3 クライアント）を持つ**ため、`endpoint_url=<起動 gateway>` に向ければ**新依存ゼロ**で実往復が
書けると判明＝前倒し解消。

- **追加テスト**: `tests/ui/test_gateway_s3client.py`（4 ケース）。
  - `test_real_s3_client_roundtrip`: PUT→GET→HEAD→ListObjectsV2(flat)→DELETE を aiobotocore で往復。ETag（PUT/GET/HEAD
    一致）・本文一致・ContentLength・削除後 GET=NoSuchKey を検証。
  - `test_real_s3_client_list_delimiter_common_prefixes`: delimiter='/' で CommonPrefixes 畳み、prefix+delimiter で
    1 階層下の Contents 列挙を実クライアントで検証。
  - `test_real_s3_client_get_missing_raises_nosuchkey`: 欠損キー GET が `NoSuchKey` 例外（XML パース経路）に。
  - `test_real_s3_client_readonly_bucket_access_denied`: writable=false への PUT が `AccessDenied`（ClientError）に。
- **起動方式**: aiobotocore は**実ソケット**を使うので in-process ASGI ではなく **uvicorn を ephemeral port
  （127.0.0.1:0）で別スレッド起動**するフィクスチャ（`_ThreadedServer`・`server.started` を待って endpoint を返す）。
  gateway は `GET /{bucket}/{key}` ＝ bucket をパスに置く **path-style** 前提なので client は `addressing_style="path"`。
- **依存の扱い＝新依存ゼロ**: aiobotocore（3.7.0）はコア依存、uvicorn（0.49.0）は `[server]` extra/dev 既存、
  pytest-asyncio は既存（`asyncio_mode=auto` で `async def test_*`）。**追加インストール不要**。同期 boto3 は入れていない。
- **実クライアント往復で判明した齟齬＝ゼロ**: 実 botocore は XML/ヘッダ厳密性に敏感だが、S1 が生成する ETag・
  Content-Length・XML 名前空間（`http://s3.amazonaws.com/doc/2006-03-01/`）・エラー Code（NoSuchKey/AccessDenied）・
  ステータスコードを **botocore がそのまま受理**＝不具合・差異なし。S1 の XML/ヘッダ実装が実クライアント基準で妥当と実証。
- **検証**: `make check` 緑（**76 passed, 1 skipped**・従来 72 から +4。skip は既存 s3-virtual E2E で本変更と無関係）。
  format-check も clean。
- **残課題**: S4 = **SeaweedFS 実機 backend** 疎通（実 client 往復は前倒し済み・残るは実 backend とパススルー）／
  S2 multipart／S3 passthrough。

## （旧フォーカス）

**M021 S1（S3 ゲートウェイ本体）実装完了（2026-06-23・supervisor interrupt 指示）。** manystore を S3 互換 API
として公開する新サブパッケージ `manystore.gateway` を追加。`m021-s3-gateway-plan.md` の S1 のみをスコープ厳守で実装
（S2 multipart・S3 passthrough・S4 SeaweedFS 実機・繰延ページング・残未決 Q1/Q4/Q5 はバックログ）。

- **新規ファイル**: `manystore/implement/s3map.py`（delimiter 畳み込み + S3 XML 生成 + エラー XML。HTTP 非依存＝
  stdlib `xml.etree.ElementTree` のみ・新依存ゼロ）、`manystore/gateway/{__init__,app,routes,__main__}.py`
  （M019 server 層と同型。`create_gateway(service)`・FastAPI 遅延 import・lifespan で connect/aclose・`[server]`
  extra 流用・`python -m manystore.gateway --config <toml>` 既定 port 9000）。
- **操作（S1）**: GET=GetObject / PUT=PutObject(ETag=MD5) / HEAD=HeadObject(Content-Length) / DELETE=DeleteObject(204)
  / ListObjectsV2(prefix+delimiter)。すべて既存 `StorageService`（put/get/exists/delete/list_entries）へ 1:1 で乗せる
  ＝**コア IF 不変**。bucket=context。delimiter は s3map で CommonPrefixes に畳む（service.list_entries は delimiter
  非対応なので gateway/s3map 側で畳む）。例外→S3 エラー XML（ContextNotFound→NoSuchBucket / ReadOnlyContext→
  AccessDenied / UnsafePathError→InvalidArgument / get None→NoSuchKey）。
- **推奨デフォルト適用**: Q2 SigV4 検証=しない（gateway 認証へ委譲）、Q6 extra=`[server]` 相乗り、Q3 ページング=
  max-keys 上限 1000 クランプ・打ち切りのみ（continuation token は繰延）。Q1/Q4/Q5・S2/S3/S4 はスコープ外。
- **テスト**: `tests/ui/test_gateway.py`(8・local backend に対し PUT/GET/HEAD/DELETE/ListObjectsV2・delimiter 畳み・
  各種 S3 エラー XML)・`tests/ui/test_s3map.py`(5・純ロジックの fold/XML)。`make check` 緑（**72 passed, 1 skipped**・
  従来 59 から +13）。⚠️ 実 S3 client（boto3/aws-cli）疎通は **S4 へ繰越**（aiobotocore は async-only、同期 boto3 は
  新依存になるため S1 では XML を ElementTree パースで検証。実 client/SeaweedFS 疎通は S4 段階）。

## （旧フォーカス）

**M020（UI 改善: パンくず階層ナビ + コピー/生パス編集）完了。** ユーザー要望（2026-06-21）:
(1) パスを `dir1 / dir2 / dir3` のパンくず表示にし各セグメントをクリックでその階層へ移動、
(2) 左にコピーボタン、パンくず（空きスペース）をクリックすると生パスのテキストボックスになり貼り付け可能。
ユーザー懸念「KVS に階層概念が薄い／中間階層に飛ぶと下層が分からない」への回答＝**問題なし**:
`/keys?prefix=` が prefix 配下の**全キーをフラットに返す**（service.list_entries が iter() を startswith 絞り込み）
ので、フロントで `/` 区切りに畳めば仮想ツリーになり、中間 prefix でも直下のフォルダ/ファイルを次セグメント
group で列挙できる（実機 smoke 済み: prefix='dir1/' で dir2/ と直下ファイルが見える）。
**実装は `manystore/server/static/`（index.html/app.js/style.css）のみ＝サーバ・protocol・python は不変**
（`pytest tests/ui` 8 passed・app.js は node --check 緑）。app.js は state.dir(現在ディレクトリ prefix)/
state.key(開いているファイル) を持ち、navigateTo→renderTree(フォルダ/ファイル畳み込み・`..`行)+renderBreadcrumb。
copy ボタンは clipboard.writeText（不可なら生パス入力にフォールバック）、新規/quick_write は editRawPath。

**M019（ストレージ UI）P1〜P3 実装完了。** `manystore.{implement,server,client}` の 3 層を追加し、任意 context を
HTTP+WS で公開する**汎用 CRUD ストレージ UI** を実装。`make check` 緑（**59 passed, 1 skipped**）＋実起動スモーク
（interrupt への remote PUT 往復を実証）。最終レイアウトは **単一ディストリビューション + `manystore[server]`
extra**（当初の別パッケージ案から巻き戻し。理由は `m019-ui-plan.md`）。interrupt 専用 UI は作らず、config の
`views.featured`（pin/quick_write）で重点表示する汎用 UI＝interrupt も「featured な local への汎用 PUT」で投入。

次サイクル候補: M019 残り（P4 http_store の RW 拡張 / P5 S3 gateway / LocalWatcher=inotify / 認証）、または
配布前提の G1（M005 redis 削除・M006 LICENSE・M007 py.typed・M008 メタ）。

（前タスク **M018 完了**。）
本プロジェクトは `agent` ブランチで単線コミットし、`interrupt/` 受信箱の指示を取り込んで進める運用。
dotfiles は `workers_dir: workers` を宣言した **supervisor**（自身も Memory Bank を持つ）で、
`dotfiles/workers/manystore` → 本 repo の symlink 配下に manystore を worker として束ねる。
下り（dotfiles→manystore interrupt 投函）／上り（manystore→dotfiles interrupt エスカレ）の双方向運用。

## 直近の変更

- **S3/NATS FileStore を完全準拠化＝「寄り」で核を配置（2026-06-23 後続・ユーザー要望/対話）**: ユーザー要望
  「s3/nats を完全準拠に。file 寄り/kv 寄りを意識し、性能が出る方に核の実装を」を実装。**S3=file 寄り**
  （streaming が強み）→ `S3FileStore(S3KeyValueStore)`＝KVS 核（native whole get/put）を継承し、open_reader/
  open_writer を **native streaming**（range body / multipart）で実装（核を IO 側に）。**NATS=kv 寄り**（whole
  get/put が native・真の streaming は nats-py のスレッド安全性で deferred）→ `NatsFileStore(NatsObjectKeyValueStore)`
  ＝KVS 核を継承し IO は **whole の上に buffer 合成**（共有 `_KvReadFileObject`/`_KvWriteFileObject` を流用＝専用
  `_NatsBufferedWriter` を削除して重複解消）。両者とも `XFileStore(XKeyValueStore)`＝「FileStore = KVS + IO」を継承で
  表現＝KVS 二重持ちなし。tests +2（fake で KVS 面 put/get/get_or_raise/iter/exists を検証・S3 fake に NoSuchKey 追加）。
  `make check` 緑（**98 passed, 1 skipped**）。残: HTTP/Safe FileStore は M027b（HTTP は read-only・Safe は委譲設計要）。
- **Protocol 関係を整理＝FileStore = KeyValueStore + IO（2026-06-23 後続・ユーザー要望/対話）**: ユーザー提案
  「FileStore Protocol に put/get/get_or_raise を含めてよい。KVS は FileStore から open_reader/open_writer を
  除いた部分集合で、それ以外はそのまま流用。KVS→FileStore は IO の埋め合わせ（ラッパ）が要る」を実装。
  (1) `class FileStore(KeyValueStore, Protocol)` に整理＝IO 2 メソッドだけ足す（put/get/get_or_raise・名前空間操作・
  ライフサイクルは KVS から継承＝重複宣言を削除）。(2) `KeyValueFileStore`(KVS→FileStore) を**完全な FileStore**に＝
  open_reader/open_writer を合成し、KVS 面（put/get_or_raise/iter/…）は下層 KVS へ委譲（open_reader は get_or_raise 経由で
  欠損 FileNotFoundError）。(3) `KeyValueFromFileStore`(FileStore→KVS) を**純委譲**に＝FileStore が put/get_or_raise を
  持つので IO 合成をやめ下層へそのまま委譲（IO を落とすだけ）。(4) `LocalFileStore` を `KeyValueStoreBase` 継承＋
  `get`→`get_or_raise`（open_reader 流用）に＝**完全な FileStore（KVS+IO）の真実の実装**。tests +2（LocalFileStore を
  KVS として／KeyValueFileStore が完全 FileStore）。`make check` 緑（**96 passed, 1 skipped**）。M027b の波及範囲は
  「FileStore の KVS 面（put/get/iter 等）を S3/NATS/HTTP/Safe FileStore にも備える」に拡大（progress 更新）。
- **KVS の get を get_or_raise primitive ＋ get(default) に再設計（2026-06-23 後続・ユーザー要望/対話）**:
  ユーザー要望「get はデフォルト値を取れる／get_or_raise（例外を上げる）を用意し、get は get_or_raise を
  捕捉する形に」を実装。**共有基底 `KeyValueStoreBase`** を新設し `get(key, default=None)` を get_or_raise から
  1 か所で実装（欠損は `FileNotFoundError` に正規化＝既存 open_reader/_kv_copy と一貫）。primitive を get_or_raise に
  反転＝`KeyValueFromFileStore`(Local)・`S3KeyValueStore`・`NatsObjectKeyValueStore`・`HttpKeyValueStore`・
  `SafeKeyValueStore`・`ArrayKeyValueStore`・`DownloadCache` を基底継承＋get_or_raise 実装に変更（各 backend の
  try/except 重複を解消）。sync ブリッジ（`AsyncToSyncKeyValueStore`）＋ Protocol（`KeyValueStore`/`SyncKeyValueStore`）も
  両メソッドに追従。`KeyValueStoreBase` を `manystore.kv` 公開（第三者 backend 実装の足場）。`_KvReadFileObject`/
  `_KvWriteFileObject` は既に CM 提供済＝get_or_raise の `async with await open_reader` 経路で活用。get_or_raise が
  下層 open_reader を**コンテキストマネージャ**で開く点も明示。tests +1（get default/get_or_raise）。`make check` 緑
  （**94 passed, 1 skipped**）。**follow-up**: `RemoteKeyValueStore`(client)・`implement/service.py` は今回スコープ外＝
  get_or_raise 未実装で Protocol 部分準拠（progress M027b に併記）。
- **M027 Local KV を FileStore から派生を実装（2026-06-23 後続・ユーザー要望/対話で設計確定）**: 前セッションで
  funnel をすり抜けて未コミットで残っていた `KeyValueFromFileStore`（壊れていた＝委譲先 LocalFileStore に
  iter/list 等が無く AttributeError、type:ignore で隠蔽）を出発点に、対話で設計を確定して完成させた。選択肢(b)
  ＝LocalFileStore を真実の実装にし、LocalKeyValueStore を `KeyValueFromFileStore(LocalFileStore)` の薄いビューに。
  FileStore Protocol を iter/list/exists/delete/cp/mv/connect/aclose で拡張し type:ignore 全廃。`KeyValueFromFileStore`
  を `manystore.kv` 公開。変更: `async_storage.py`・`backends/local.py`（全面再構成）・`kv.py`・`tests/test_storage.py`(+2)。
  `make check` 緑（93/1）。詳細は上記「現在のフォーカス」。
- **バッファ性方針の確定＋Local KV 派生方向をバックログ起票（2026-06-23・ユーザー要望/対話）**: 対話入力を
  interrupt（`20260623-local-kv-from-filestore-and-buffer-semantics.md`）へ funnel→トリアージ→archive。(1) 設計方針
  **「KV=バッファ概念／FileStore=バッファ無し概念。adapter でどちら向きに被せても KV 層でバッファ＝みせかけのストリーム。
  真にバッファ無しは生ストレージを素通し露出した時だけ。サーバ越しに真の streaming は出せず、真髄はクライアント wrap」**
  を **systemPatterns 原則6** に昇格。(2) 具体要望「Local の KV を `KeyValueFromFileStore(LocalFileStore)` で派生
  （メイン実装を LocalFileStore に集約）」を **progress M027（設計先行・相談）** に起票。設計の壁＝FileStore Protocol に
  list/exists/delete が無く get/put しか派生できない＝doc-first 合意要。
- **stream インターフェース（第3の族）をバックログ起票（2026-06-23・ユーザー要望/対話）**: storage/kv に加え
  **stream**＝単一ターゲットに接続を張り続け追記/追従するチャネル族（jsonl 追記・NATS トピック=ファイル・MVP=バイト）。
  FileStore で表現できない **tail/subscribe＋継続 append**＝**新コア IF**ゆえ facade では済まず doc-first 合意が要る。
  interrupt へ funnel→archive、progress に **M026（相談・設計先行）**として起票。
- **M025 名前空間再編フェーズ1（移設）を実装（2026-06-23・ユーザー要望/対話）**: 対話で要望を受け interrupt
  （`20260623-namespace-restructure-kv-storage.md`）へ funnel→トリアージ→`interrupt/archive/` へ退避。設計を
  `m025-namespace-restructure-plan.md` に確定（buffer 性で `kv`/`storage` に二分・4 ルート）。フェーズ1＝combined の
  prefix を `/manystore`→`/kv/raw`・`/s3`→`/storage/s3` に付け替え（上記「現在のフォーカス」）。progress に M025 起票。
  変更ファイル: `combined.py`・`__main__.py`・`tests/ui/test_combined.py`。`make check` 緑（91/1）。
- **interrupt 受信箱を取り込み（2026-06-23・M023 着手時）**: 2 件をトリアージ。(1)
  `20260622-stage2-quality-resolved-and-pull-migration.md`（info+low）= quality エスカレ解決の共有は受領（追加作業なし）、
  pull 型移行＋層エイリアス統一の残タスクを **progress.md M024（priority low）**へ起こした。(2)
  `20260623-s3-gateway-and-passthrough.md` = M021 の原指示で `m021-s3-gateway-plan.md` に既吸収＝archive へ退避。
  両ファイルを `interrupt/archive/`（日付プレフィクス）へ移動。
- **M023 統合エントリポイント化を実装（2026-06-23・supervisor 指示）**: `include_router(prefix=...)` で `/s3`+`/manystore`
  を 1 アプリに束ねた（上記「現在のフォーカス」）。新規 `combined.py`・`__main__.py`・`tests/ui/test_combined.py`、
  変更 `server/routes.py`・`gateway/routes.py`（`build_router` 追加・`register_routes` をシム化）。`make check` 緑（91/1）。
- **M021（S3 ゲートウェイ + パススルー）の着手前 deep think を実施（2026-06-23・supervisor interrupt をトリアージ）**：
  `interrupt/20260623-s3-gateway-and-passthrough.md`（priority normal）を取り込み、**実装はせず設計のみ確定**。
  成果物 `m021-s3-gateway-plan.md`。要旨＝(1) 最小 S3 操作 = GET/PUT/HEAD/DELETE/ListObjectsV2（multipart は段階2、
  bucket=context、delimiter 対応）。(2) パススルー = presigned redirect 本線＋プロキシフォールバック、署名は
  **gateway 保有の実 S3 資格情報**で代理署名（クライアント資格情報は実 S3 へ転用しない＝STS は YAGNI）。
  presign は **S3 backend 限定の optional capability `SupportsPresign`** に閉じコア IF は不変。(3) 置き場 =
  新サブパッケージ `manystore.gateway`（M019 同型・FastAPI 遅延 import・既存 `[server]` extra 流用・S3 XML は
  stdlib ElementTree＝新依存ゼロ・`implement` の `StorageService` を再利用）。(4) 段階 = S1 本体→S2 multipart→
  S3 パススルー→S4 SeaweedFS 実機。**コード未実装・git commit せず**（WIP なし。コミット判断は worker 対話/ユーザー）。
  既存 progress の M019 P5（S3 gateway）を本計画として精緻化・採番。

- **検証はベタ書き禁止＝Makefile 経由に統一（ユーザー要望 2026-06-21）**：techContext.md の「検証コマンド」を
  生 `uvx ruff …` → `make lint`/`make check` 参照に修正（ruff 版は `RUFF_VERSION := 0.15.18` で固定済み・既に
  Makefile 完備）。これが「毎回手打ち」の元凶だった。**この方針の正本は quality スキル（R5 Makefile / R8 `make check`）**。
  - **dotfiles の位置づけを再訂正（陳腐化）**：dotfiles は今や `workers_dir: workers` を宣言した **supervisor**で、
    `dotfiles/workers/manystore -> ../../manystore` の symlink 配下に manystore が worker としてぶら下がる。
    過去メモ「dotfiles は supervisor でない」は無効。
  - **親へエスカレ実施**：「quality が常時ループ（memory-bank）から参照されず発揮されない」構造ギャップを、
    親 `dotfiles/.work/skills/memory-bank/interrupt/20260621-quality-skill-not-applied.md` に worker として投函
    （memory-bank→quality のリンク追加等を提案。反映先は supervisor 判断）。親スキルは worker から直接編集しない。
- **UI 開発起動を整備**：`make ui`（= `examples/manystore-ui.dev.toml` で起動）。dev 既定ストレージは
  `.cache/manystore_dev`（`.gitignore` に `.cache/` 追加＝使い捨て・起動時に LocalKVS が自動 mkdir）。
  `PORT=xxxx` で上書き可。client SDK は `ManystoreClient`/`RemoteKeyValueStore`（`manystore.client.remote`）に改名済み。
- **公開 API 整理（ユーザー要望 3 点）**：
  - **pytest-asyncio 導入**（`asyncio_mode="auto"`）。新 UI テスト（implement/client）は `async def` 化。実害なし
    （dev 依存のみ・既存 `asyncio.run` と共存・fastapi TestClient は同期で anyio 競合なし）。
  - **名前空間グルーピング**：`manystore.kv`（値ストア）/ `manystore.file`（ファイル）facade を新設。トップは
    後方互換でフラット再エクスポート（star import + noqa、`__all__` は dict.fromkeys 重複畳み込み）。
  - **FileStore を方向別バイナリ API に置換**：`open(mode)` 廃止 → `open_reader`/`open_writer`。全 backend・
    KeyValueFileStore・SafeFileStore・SyncFileStore Protocol・`tests/test_storage.py`（一括置換）を更新。
    HttpFileStore は read-only で `open_writer` が `io.UnsupportedOperation`。`make check` 緑（59 passed, 1 skipped）。
- **M019 P1〜P3 実装（ストレージ UI）**：`manystore/implement`（protocol/config/service/watcher・backend非依存）、
  `manystore/server`（FastAPI app/routes/__main__/static・遅延 import）、`manystore/client`（ManystoreClient /
  RemoteKeyValueStore）を追加。`pyproject` に `[project.optional-dependencies] server` と dev group。
  `tests/ui/`（implement/server/client の 3 層）。`examples/manystore-ui.toml`・README 節を追加。
  - **決定の巻き戻し**：当初「別パッケージ（uv workspace）」→ ユーザー選択で **`manystore` 配下に 3 層サブ
    パッケージ＋extras** に確定（import 名前空間 `manystore.*` 統一、配布は extras+遅延 import で軽さ維持）。
  - 監視は MVP では **PollingWatcher**（size 差分で created/modified/deleted、全 backend 対応・テスト容易）。
    inotify(watchdog) ベースの LocalWatcher は後続最適化。`modified` は同一サイズ編集を取りこぼす既知制約。
- **M018 完了（HTTP backend, read-only）**：ユーザー要望「http ストレージを read-only でよいから欲しい」を実装。
  `backends/http_store.py`（GET で `get`/`open("rb")`、HEAD で `exists`。404→None/FileNotFoundError。書き込み・
  一覧は `io.UnsupportedOperation`）。httpx を遅延 import。`create_key_value_store("http", http_base_url=...,
  http_headers=...)` 配線、`__init__.__all__`・README・テスト（fake httpx client で 4 ケース）整備。
  - **モジュール名**: 当初 `http.py` で作られていたが stdlib `http` パッケージと紛れるため `http_store.py` に
    リネーム（**backend 識別子は `"http"` のまま**）。ユーザー指摘。
  - **M005 修正**: httpx は当初「未使用＝削除」だったが http backend で使うので**残す**に変更。`redis` のみ未使用。
- **プロセスの穴を2つ発見し、フックで修正**：
  - (a) ユーザー要望（http backend）が**着手前に Memory Bank へ保存されず**、前回セッションで未コミットの試作だけ
    残っていた（活動記録なし）。教訓は「要望は着手前に activeContext/タスクへ記録してから実装」。
  - (b) **より根本**：SessionStart フック `dotfiles/bin/memory-bank-sessionstart` が「`.work/skills/memory-bank/`
    の**ディレクトリ有無だけ**」を見ていた。① 完全に無ければ黙って no-op（警告なし）、② dir が在れば中身が空でも
    「Memory Bank があります」と誤検知。つまり **Memory Bank の成立条件（`.work`＋6コア）が崩れても誰も警告しない**。
    → フックを「**dir はあるがコアが欠けていれば警告して initialize を促す**」よう修正・実測検証（dotfiles 側の作業
    ツリー変更。コミットは dotfiles 側に委ねる）。完全欠如はマーカー無しに全 repo で鳴らせないので no-op のまま。
  - **dotfiles の位置づけを訂正**（※**この訂正は後に覆った**——上記 2026-06-21「dotfiles の位置づけを再訂正」参照）：
    当時は「dotfiles はスキルのホストであって Memory Bank を持つ supervisor ではない」と判断したが、**現在の dotfiles は
    `workers_dir: workers` を宣言し 6 コアの Memory Bank を持つ正式な supervisor**（manystore を `workers/` に symlink 配下）。
    下り（dotfiles→manystore interrupt 投函）は当時から実績あり（M003 の m003-ci 指示）＝この点だけは一貫。
- **UI 要望をバックログ化**：ユーザー要望「ストレージの UI が欲しい」を progress.md の **M019（相談）**へ。
  未スコープ＋本体スコープ外のため、別パッケージ/別リポか着手前に要合意。

- **juice 概念を削除**：manystore は juice と無関係な独立ライブラリなので、コード（`__init__`/`array_storage`/
  `tests`/`pyproject`/README）と Memory Bank から juice・E006・「pristine（juice 都合）」の記述を一掃。設計
  原則は「**最小・汎用に保つ（YAGNI）**」として残す。juice adapter のバックログ（旧 M005）も削除。
- **M002 一部完了**：docker（nats / seaweedfs）で `tests/test_e2e_backends.py` を**パラメタライズ**追加
  （同一 CRUD を local / nats / s3-virtual / s3-path に注入。実行 test は1つ、注入インスタンスだけ違う）。
  **local / nats は実機で pass**。S3 は実機検証で **アドレッシングスタイル問題を発見**し、`addressing_style` を
  **明示パラメータ化（既定 virtual＝ドメイン、`"path"` は opt-in）**に変更（`s3_addressing_style`）。
- **M002 完了**: SeaweedFS の S3 認証は `weed shell s3.configure` で dev identity（`manystore`/`manystoresecret123`,
  Admin）を登録して解決。`make e2e-up`（compose up + identity 登録）で 1 コマンド化し、テスト既定鍵もこの dev
  identity に。`make check` で **s3-path 実機 pass**（47 passed, 1 skipped）。s3-virtual はローカルでは原理的 skip。
- **M004 完了**：ルート `README.md` を作成（特徴・install・local/S3/NATS の接続例・`ConnectPolicy` プリセット・
  `Safe*` ラッパ・その他公開 API・開発/CI/3.14 注記）。公開 API は `manystore/__init__.py` の `__all__` に準拠。
- **M003 完了（supervisor 指示で着手）**：dotfiles（supervisor）が manystore の interrupt に投函した指示
  （`20260620-1200-m003-ci.md`, priority high）を取り込み、GitHub Actions CI（`.github/workflows/ci.yml`：
  setup-uv → `make check`）を追加。指示は `interrupt/archive/` へ退避。
- **Python 3.14+ 前提を確定**：3.14 は注釈遅延評価が既定なので前方参照（自クラス戻り値注釈）はそのまま valid＝
  `from __future__ import annotations` 不要。`requires-python = ">=3.14"` ＋ ruff `target-version = "py314"` に
  し future import を全廃。ruff は py314 対応版が要るので `RUFF_VERSION` を 0.15.18 へ。`make check` 緑（44 passed）。
- **M001 完了**：旧名残骸を監査（`git grep shoudou`）。実コードの残骸は NATS 既定バケット名のみで、
  `manystore/backends/__init__.py` の `nats_bucket="shoudou_files"`→`"manystore_files"` に変更（既定値のみ・
  テスト非依存）。`uv run pytest` で **44 passed**。
- 本セッションで `Makefile`（`uvx ruff@<固定版>` の format / `uv run pytest` の test）を追加（M003 の一部）。
- `shoudou_storage` を独立ライブラリ `manystore` として抽出し、import 名・プロジェクト名を `manystore` に
  統一。関連 commit: `f80ba87` / `1983fc7` / `2d28010`。
- Memory Bank を導入。当初は AGENT_LOOP.md / PROJECT.md の 2 ファイル構成だったが、
  **Cline の Memory Bank（6 コアファイル）に準拠**するよう作り直し、作業フォルダ
  `.work/skills/memory-bank/` 配下へ集約した。

## 次のステップ

- バックログ（progress.md）から優先タスクを 1 つ選定し、本ファイルの「現在のフォーカス」に展開。

## 進行中の決定・考慮事項

- **Memory Bank は Cline 準拠の 6 ファイル**（projectbrief / productContext / activeContext /
  systemPatterns / techContext / progress）。手順・運用は共通スキル `memory-bank`（`~/.claude/skills/`）に集約。
- 作業フォルダ規約は `.work/skills/<スキル名>/`。`.work/` は gitignore しない（状態の正本＝commit する）。
- **コミットをフローに組み込む**：Act Mode の終端で「切りのいいところ」（まとまり一段落＋検証緑＋
  Memory Bank 更新済み）になったら、コード＋Memory Bank を 1 コミットにまとめる。`main` 直は避け branch を切る。
  push は明示時のみ。
- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない。利用側固有の結線は利用側の adapter に閉じる。

## 重要なパターン・好み / 学び

- **フローは全て interrupt を介す＋参照系は reference/**（memory-bank 設計変更, 2026-06-21）：対話での要望・指示も
  着手前に一旦 interrupt へ書き出してから取り込みフローで処理する（即答の雑談は除く）。横断的な参照定義・要件は
  `reference/`（ファイル/ディレクトリ）に集約し、品質方針はその 1 エントリ（`reference/quality-policy.md`）。品質以外の
  要件も reference に足せる。コア/SKILL 本体は中身を持たず reference を参照するだけ。
- **品質チェックは組織の品質方針に従う（関心の分離）**：memory-bank は「品質チェックを行う」だけ・規約を持たない。
  一般メソッドは [[quality]]、組織固有の適用は **組織の品質方針ファイル**（supervisor memory-bank `reference/quality-policy.md`）、
  本 repo の techContext はそれを **`make check` に materialize するだけ**。検証は `make` 経由＝ベタ書き `uvx ruff …`
  禁止（再現性）。スキル設計（dotfiles）も更新: memory-bank を最小化＋ reference/ 導入／quality に「関心の分離（俯瞰的/単体）」
  「ドキュメントの書き方・読み方」節＋R10/R11／supervisor が drift を定期チェック。
- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる必要があった（過去バグ）。
