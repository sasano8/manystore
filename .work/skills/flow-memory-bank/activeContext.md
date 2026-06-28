# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

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
