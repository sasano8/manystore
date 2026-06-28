# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

**品質優先・拡張後回しへ方針転換**（2026-06-28・ユーザー指示）＝現プロダクトの品質を高めるタスクを最優先で
消化する。4 観点のコード監査（error handling / lifecycle・並行 / test coverage / DRY・API 一貫性）を実施し
**品質強化タスク M054〜M064 を抽出・登録**（high 指摘は実コードで裏取り済）。拡張的タスク（M051/M039/M040/
M026/M045/M028b＝新 surface/backend/IF）は品質強化が一段落するまで着手しない。**M054/M055/M059 完了**（2026-06-28）＝fail-loud バグ修正（nats get_or_raise の narrowing／remote exists の
5xx 伝播・test +3）＋**pytest-cov 導入**（`make cov`・ベースライン TOTAL 77%）。
**方針＝実装漏れは conformancer に契約として実装し横断検知**（北極星＝conformance を仕様の単一源泉に＝
projectbrief「北極星」）。**M065 step1+step2 完了**（2026-06-28）＝step1: run_middle 本実装＋絶対契約
`assert_writer_aborts_on_error` で M058 を契約先行修正。step2: **fault-injection で fail-loud を契約化**＝
番兵 `InjectedFault`＋`FaultInjectingKeyValueStore`＋`assert_fail_loud_propagation`（wrapper が下層障害を
None/False/default/NotFoundError に化けさせず伝播するかを横断検査＝M054/M055 クラス）。base/Safe/
KeyValueFileStore/DownloadCache を契約ロック＋牙テスト。**step3 完了（2026-06-28）＝契約カタログ→spec 文書
生成**（北極星③④）＝`ContractSpec`/`ABSOLUTE_CONTRACTS`（絶対契約宣言）＋drift ガード
`assert_contract_catalog_current`（カタログ↔実装の同期＝仕様だけでテスト無しを防ぐ）＋`differential_contract_aspects`
（差分観点を run_* 実行から導出）＋`__main__` が `docs/conformance_spec.md` 生成（mkdocs nav 追加・--strict 緑）。
conformance 25→38。
**M066 step1+2 完了（2026-06-28）＝挙動契約の集約ハーネス**＝`tests/conformance_providers.py` に
全 provider（dict/local/remote/nats/s3）を **1 か所宣言**（open→接続済み FileStore・gated・isolated・reachable）
＋`tests/test_conformance_matrix.py` が全契約を流す。**M066① 完了（2026-06-28・本サイクル）＝run_* 非破壊化**
＝run_light/run_middle を delete_all 全消去から **uuid 名前空間スコープ＋`_cleanup_ns` 後始末**へ転換（列挙/状態を
`iter_all(prefix=ns)` に閉じる）。`isolated` 廃止し **run_light/run_middle も全 provider（実 nats/s3 含む）で実行**。
**実 nats・実 s3-path で緑を確認**（`make e2e-up`→gated matrix・10 passed/8 skip）。fast 185。残＝② leaf backend
（nats/s3）の fail-loud transport fault（backend 固有 fault transport が要る・M065 連動）／③ native FileStore writer 直接検証。**M065 step4 完了（2026-06-28・北極星④）＝scaffold 自動生成**＝`scaffold_backend(class, kind)`＋CLI
`--scaffold MyStore --kind kv|file`＝基底の `__abstractmethods__` を Protocol 署名で stub し満たすべき契約
TODO＋配線手順を出力（契約一覧＝実装の TODO・matrix の provider に通すだけで実装漏れが loud）。test +4。
これで北極星①〜④が一通り実装（①テスト②cov③spec 文書④scaffold）。**M065 step5 完了（2026-06-28・M066 step3 連動）
＝fail-loud を transport 越しでも契約化**＝契約を「型問わず raise／NotFound・正常終了に化けさせない」へ精緻化し
`assert_fail_loud_over_transport` を新設。500 fault transport の RemoteKeyValueStore に当て全 op の非握り潰しを
契約化（M054/M055 を HTTP 越しでも横断検知）。**北極星①〜④＋fail-loud（in-process/transport）完備**。
**M065 step6 完了（2026-06-28・本サイクル）＝run_heavy 本実装**＝規模・境界の差分契約（128 KiB 多チャンク
round-trip／チャンク境界非依存の分割 read〔新 op `open_reader_read_segments`〕／多キー昇順／grow→shrink→regrow
連続 overwrite）。非破壊（M066① の ns スコープに乗る）＝全 provider（実 nats/s3 含む）で実行。spec 文書生成に
heavy を追加（北極星③）。test +3＋matrix +1。実 nats・実 s3-path で緑。`make check` 緑（191）・mkdocs --strict 緑。
**M065 step7 / M066② 完了（2026-06-28・本サイクル）＝leaf fault transport**＝fail-loud over transport を **実 leaf
backend（nats/s3）へ**拡張（従来は HTTP 越し Remote のみ）。接続後に下層クライアントを故障プロキシへ差し替える
opener（nats=`store._obs`→`_FaultObjStore`／s3=`store._session`→`_FaultS3Client` を yield する CM）＋
`leaf_fault_providers()`＋matrix `test_fail_loud_over_transport`（gated）で `assert_fail_loud_over_transport` を
当てる。**この契約が nats `delete` の M054 級握り潰しバグ（`suppress(Exception)`）を炙り出し `except JSNotFound`
へ narrowing で是正**。実 nats-fault・実 s3-path-fault で緑＋crud/run_middle も緑。
**M066③ 完了（2026-06-28・本サイクル）＝native FileStore writer 直接検証＝M066 完了（残なし）**＝KVS-native を
`KeyValueFileStore` で包むと writer がバッファ経由になり S3 の native streaming IO を検査できていなかった。
native `S3FileStore` を直接接続する `native_file_providers()`（`s3-path-native`）＋matrix
`test_native_writer_aborts_on_error`／`test_native_file_io_matches_oracle`（run_light/middle/heavy を native IO で）。
**この契約が `_S3MultipartWriter.__aexit__` の all-or-nothing バグ（例外時も complete し中途確定＝M058 級）を
発見・是正**＝`_abort()`（abort_multipart_upload）を新設し `exc[0] is not None` で確定しない（local と契約統一）。
teeth-test で違反検知も確認。実 s3-path-native で緑。⚠️既知の弱点＝gated は contract の AssertionError も skip に
化ける（harness 改善は別タスク候補）。**M065 残＝非 CAS 並行／run_full。M066 完了**。
**品質監査 Tier A〜C 完走（2026-06-28）**＝M054/M055（fail-loud）・M056（nats lock）・M057（lifecycle ロールバック）・
M058（writer all-or-nothing）・M059（pytest-cov）・M062（list_all 基底集約・47行減）・M063（同期FS の非同期ヘルパ化）・
M064（cp/mv identity 判定＝保守設計を明記＋テスト固定）。加えて M060/M061（crypto pytest 化・実 backend e2e 強化）は
未着手で残（test 強化）。M065（conformance を仕様の単一源泉に＝北極星①〜④＋fail-loud transport）完了。
**次サイクル候補**＝M060/M061（テスト強化）／M013・M012・M021残・M025残 等（機能・完成度）／progress 肥大化につき
`memory clean` 提言。**拡張（M051/M039/M040/M026/M045）は方針どおり後回し**。詳細は progress「品質強化」。

**M044 完了**（2026-06-28・ユーザー対話で着手）＝spec/既定値の定数集約。**専用 `specs.py` は作らず
`protocols.py` 冒頭に「spec/既定値」節**を新設（ユーザー確定＝定数の正本をインターフェースと同居・データ専用・
ロジックは寄せない）。core 共有値のみ正本化＝`DEFAULT_LIST_LIMIT=1000`（service/server routes/remote client/
conformancer）／`MAX_HTTP_LIST_FETCH=10_000`（remote の HTTP fetch 上限）。S3 仕様由来値（`DEFAULT_MAX_KEYS`・
partNumber 範囲）と単一使用の `DEFAULT_CACHE_DIR` は所有モジュールに据え置き（locality 優先）。`make check` 緑（152）。


**M049/M050 完了**（2026-06-27・ユーザー対話で着手）＝① `create`（create-if-not-exists・非原子の派生／既存は
`ConflictError`）を `_StoreBase` 既定実装に追加（lockstep 維持）／② 2 ストア片方向同期 `StorageMirror` を
新パッケージ `storage/sync/` に新設（集合差 reconcile・source 正・prune は opt-in）。`make check` 緑。
**M052/M053 完了**（2026-06-27）＝M052: テスト 75 箇所を `async def` 一括移行（挙動/件数不変・以後 async def 標準）。
M053: 欠損を `NotFoundError(FileNotFoundError, ManystoreError)` へ昇格（src の生 FNF を全正規化＝local open_reader の
OS 生 FNF・s3 native streaming の NoSuchKey も含む／tests を NotFoundError へ厳格化／破壊変更ゼロ）。
**M046 MVP 完了**（2026-06-28・ユーザー対話で実装）＝conditional put を **put 1 本＋任意 `if_match`** で実装
（派生メソッドなし）。None=LWW／ABSENT=create CAS／FileInfo=update CAS（不一致 `ConflictError`）。`head` 新設・
dict メタストア・local os.link/flock・全 wrapper 委譲・M043 parity 緑。conformancer が実ストア経由で並行安全性を強制。
詳細は progress 完了マイルストーン。残＝M046残（NATS CAS / serving 配線 / remote）。
**M046残 remote 署名検証 完了**（2026-06-28・案B「HTTP 越し conformance」step1・ユーザー対話＝**今回は署名検証のみ**に
スコープ確定）＝conformancer に concrete store 用署名ヘルパ `concrete_store_signature_errors`/
`assert_concrete_store_signatures`（メンバ存在＋param 署名一致・戻り narrowing は許容＝base↔Protocol lockstep の
strict parity と棲み分け）を新設し、`RemoteKeyValueStore`↔`AsyncKeyValueStore` の param drift ゼロを test で固定。
**M046残 serving 配線＋remote 条件 put 完了**（2026-06-28・案B step2/3・ユーザー指示「M046 を進めて」）＝conditional
put を **native REST と remote に end-to-end 配線**し **CAS 並行安全性を HTTP 越し conformance で機械検証**。routes は
PUT が `If-None-Match: *`/`If-Match: "<etag>"` を `if_match` に解き HEAD が `ETag`＋独自 size/modified_at を露出、
不一致は既存 problem(409)。service に `head`/`head_or_absent`＋`put(if_match)`。remote は条件ヘッダ送出＋`head`
override（HEAD メタから version FileInfo）＋409→`ConflictError`。test +4（conformancer の create/update CAS チェッカを
HTTP 越しに回す）。`make check` 緑（146）。
**M046残 NATS revision CAS 完了**（2026-06-28・ユーザー指示「NATS revision CAS を実装」・**実 NATS で検証**）＝NATS
Object Store の条件 put を実装。version=メタ subject の最終 stream seq、メタ publish に
`Nats-Expected-Last-Subject-Sequence` を付けてサーバ側原子 CAS（10071→`ConflictError`）。create-only=baseline seq・
update CAS=etag(=seq)・head は seq を etag に。`_put_with_occ` で bytes をチャンク＋メタ最小再実装（object store 内部
ワイヤ依存）。docker compose の実 NATS で conformancer 並行チェッカ＋多チャンク roundtrip 緑。e2e に
`test_backend_conditional_put_cas`（local 常時／nats・s3 gate）追加＝local/nats 緑。⚠️作業環境異常が**再発**＝
`except (A,B):` を py2 構文へ書き戻された→単一クラス catch で回避（[[discipline needs automated backstop]] 系）。
`uv run pytest` 全緑（158 passed）。
**M046 全面完了**（2026-06-28・ユーザー指示で S3 GW If-Match/If-None-Match 配線）＝S3 互換ゲートウェイの PutObject に
conditional write を配線し M046 を完結。`If-None-Match: *`→create-only／`If-Match: *`→存在要求／`If-Match: "<etag>"`→
ETag(本体 MD5)突合＋backend version トークンで原子 CAS。precondition 不成立=412 PreconditionFailed、非対応形=501
NotImplemented。gateway の ETag は MD5 のまま（既存契約）＝backend CAS トークンと別物のため MD5 突合＋head version の
二段で橋渡し。test_gateway +5。これで **全 backend＋native REST＋remote＋S3 GW** が conditional put/CAS 配線済。
`uv run pytest` 全緑（**163 passed**・4 skip=s3 のみ）。詳細 progress。
**M046 設計の要点（完了済・確定事実）**＝派生メソッドは作らず **put 1 本＋任意 `if_match: FileInfo|None`**（None=LWW／
不在 FileInfo=create-only／FileInfo=update CAS・不一致 `ConflictError`）。比較トークンは `FileInfo.etag` に畳む
（S3=ETag・local=mtime_ns+size・dict=世代・NATS=メタ subject の最終 seq）。`head(key)->FileInfo` が version 読み口。
設計 plan は GC 済（恒久事実は systemPatterns・経緯は git 履歴）。

## 直近の変更

> 完了マイルストーンの詳細は `progress.md` に集約。ここには溜めない（重複は memory clean で畳む）。

- **M043 完了**（最重要・supervisor 指示で最優先実装）＝基底↔Protocol の lockstep を①共通基底 `_StoreBase` の
  全面 abstract/既定 ②conformancer parity assert で是正。波及で http/s3/nats/ipfs の基底列挙順を mixin 先置へ。詳細 progress。
- interrupt 4 件取り込み・archive 退避＝①supervisor 指示（M043 先行・横展開はブロッカー扱い・横断昇格は MVP 後）
  ②supervisor 指示（反省 metrics は手記録 MVP・2 ヒューリスティック即運用・新スキルは作らない）③TODO 規約 親正本反映済
  ＋次サイクルで `make grep-todo` sweep して孤児 TODO 点検（→済＝M040/M041/M042/M044 の既知 id のみ・孤児なし）
  ④user 要望 put→FileInfo（**確認＝既に全 backend 実装済**＝No-op。lockstep は M043 parity が担保）。
- **ユーザー指摘（2026-06-27・対話）**＝atomic write は排他していない／並行更新で先勝ちが起きる。→ 方針を
  意思決定の変遷に記録し **M046**（conditional put / lost-update 検出）としてバックログ化（相談・doc-first）。
- ⚠️**作業環境の異常**: `conformancer/__init__.py` の `except (TypeError, ValueError):` が編集後に
  Python2 構文 `except TypeError, ValueError:`（SyntaxError）へ**外部から繰り返し書き戻された**。`except Exception:`
  へ書式変更して回避・緑維持。原因不明（hook/インジェクション疑い）。ユーザーに要報告。
- TODO 規約＋`make grep-todo` 要望：配置を unit-quality（定義）／supervisor・flow（参照）に合意。**親正本の skill は
  worker から編集不可**（ガード発火＝役割モデル）→ `outbox/2026-06-26-todo-convention-and-grep-todo.md` に上りエスカレ。
  repo ローカルは実施：Makefile に `make grep-todo` 追加＋既存マーカー4件を `# TODO(<id>)` 書式へ整合（M040/M041/M042 を backlog 化）。
- interrupt `2026-06-26-ipfs-and-loadbalancer.md` を取り込み＝IPFS backend／ロードバランサー層の **scaffold 要望**。
  意見すり合わせ（IPFS=MFS 主／LB=負荷メトリクスで選ぶ動的プレースメント・Array の兄弟）の上、空定義＋ネタを配置。
  factory/facade には未接続（未完成のため・ユーザー指示）。→ archive 退避済。残作業 M039/M040 として継続。
- interrupt `2026-06-26-stream-cipher.md` を取り込み＝`manystore.crypto` 新設要望。最小実装＋インライン self-test の
  みでテスト/ストレージ実装はせず、IO 繋ぎこみ IF の明確化に絞る方針で archive へ退避済。M038 を実装・完了。
- interrupt `2026-06-25-m010-async-file-lib.md` を取り込み＝既存 backlog M010 の方式論点を精緻化。`aiofile`（真 async）
  より `anyio`（スレッドプール系・在中）を採用（理由: buffered では native AIO もスレッド fallback・移植性/最小優先）。
  → archive へ退避済。M010 を実装・完了。

## 次のステップ

- **最優先候補＝M043（ABC 基底 ↔ Protocol 契約の lockstep 保証・最重要）**。着手時は是正案①〜③から選定。
- 実装サイクル候補（`progress.md` 残作業から）: **M011（安全入口の最終形）**＝下記「進行中の決定」の命名/格下げ/
  Array enter_context を実装／フェーズ2 `kv/json`／フェーズ3 `storage/manystore`。

## 進行中の決定・考慮事項

- **【完了】安全入口の最終形（M011）**: 入口の命名マトリクス（3×3）を確定＝**unsafe**（`create_unsafe_{key_value,file}_store`
  ＝生・未接続・キー検証なし／array は `ArrayKeyValueStore` 直）/ **safe**（`create_safe_{key_value,file,array}_store`
  ＝Safe 包装・未接続）/ **顔**（`open_async_{key_value,file,array}_store`＝Safe 包装＋接続 CM）。生口はトップ公開に残す
  （ユーザー確定＝格下げせず名前で明示のみ）。`ArrayKeyValueStore.mount`/`unmount` は登録のみ（**IF は
  非同期化済＝将来 M028b の動的マウントで `asyncio.Lock` を後付けできる余地。本体は現状 I/O なし**）。
- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない（YAGNI）。
- **worker/supervisor**: 本 repo は dotfiles（`workers_dir: workers`）配下の worker。下り=interrupt 投函／
  上り=`outbox/` へ pull 型エスカレ（親は直接知らない）。
- **MB 運用**: Cline 準拠 6 コア＋`plans/`（完了 plan は削除・残フェーズの plan のみ保持）。`.work/` は commit、
  コミットは「切りのいいところ」でコード＋MB を 1 コミット、`agent` ブランチ単線、push は明示時のみ。

## 重要なパターン・好み / 学び

- **設計原則の正本は repo の `docs/architecture.md`**（FileStore=KVS+IO・核は native primitive 側・conformance）。
  Memory Bank は一時記憶ゆえ要約のみ。
- **フローは全て interrupt を介す＋参照系は reference/**: 対話の作業要望も着手前に interrupt へ書き出してから取り込む。
- **品質チェックは `make` 経由**（`make format`/`make test`＝fast・`make test-all`＝全部）。ベタ書き `uvx ruff …` 禁止。
  ※`make test` は lint を回さない＝format ドリフトは別途 `make format` で検出（過去に CJK 行の E501 が埋もれた）。
- **3.14 前提で `from __future__ import annotations` は全廃**（PEP 649 で前方参照は valid）。新規ファイルにも入れない。
- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる（過去バグ）。
- KV=バッファ概念／FileStore=バッファ無し概念。真の streaming はクライアント wrap で得る。
