# M046: put の並行更新（conditional put / lost-update 検出）設計計画（deep think・着手前ゲート）

> 指示の正本: ユーザー対話（2026-06-27）＝「atomic write は排他していない／先勝ちが起きる、失敗すべき」。
> 合意済の方針: **失敗判定は mtime 単独でなく version/etag の compare-and-swap（CAS）**。local では mtime(+size)
> を etag 的トークンに使ってよい（modern FS は ns 精度）。本ファイルは **doc-first の設計成果物**。実装はしない
> ＝設計と段階計画・未確定点の確定まで。命名は M021/M025 plan の慣習に倣う。

## 設計改訂（2026-06-28・ユーザー確定）＝**派生メソッド撤回・put 1 本＋`if_match`**

> 以下が**最新の正本**。本セクションと矛盾する下部記述（`put_if_absent`/`put_if_match` を別メソッドで
> core に足す案・opaque `version: str` 引数）は**撤回**。経緯は git 履歴に残す。

- **派生メソッドを作らない**: `put_if_absent`/`put_if_match` という別メソッドは**不要**。堅牢性は
  「1 つの挙動」を put の標準にする。core 表面を太らせない（ユーザー判断）。
- **API は put 1 本＋任意キーワード `if_match`**:
  `async def put(self, key, value, *, if_match: FileInfo | None = None) -> FileInfo`
  - **`if_match` 省略**（既定）＝**原子＋直列化の無条件 put（LWW）**。torn write/interleave を防ぎ
    常に丸ごと着地するが**失敗はしない**（last-writer-wins）。並行変化の検出は呼び出し側が `head()` で行う。
  - **`if_match=<head で読んだ FileInfo>`** ＝**update CAS**。読んだ版から変わっていれば `ConflictError`
    （lost-update を検出して fail-loud）。当初要望「先勝ち上書きは失敗すべき」を満たすのはこの経路。
- **opaque な `version: str` 引数は出さない**: 呼ぶ側が触るのは `head()` が返す **`FileInfo` そのまま**。
  比較用の backend ネイティブトークン（S3=ETag・local=mtime_ns+size・NATS=revision・dict=世代）は
  **`FileInfo` の中に畳む**（呼び出し側は解釈しない）。
- **ファイル情報取得メソッド `head(key) -> FileInfo` を新設**（put 派生でない読み取り系）。
  `FileInfo` に **`modified_at`（更新日付）** を追加＝`{filename, size, modified_at, <native token>}`。
  - put の戻り値は現状どおり**安価な subset**（`{filename, size}`・追加 I/O なし）。mtime/native token は
    `head` が読む（S3=HeadObject・local=os.stat・NATS=get_info）。
  - **dict backend は素の `{key: bytes}` ゆえメタストアが必要**＝`{key: FileInfo}` を併設し put 時に
    値とロック下で同時更新（`if_match` 省略時の head 成立にも必要）。`modified_at` は実クロック・世代は
    カウンタ。
- **スコープ（MVP・2026-06-28 更新）＝update CAS ＋ create 競合の両方**: 当初 create 競合をスコープ外と
  したが、「put_if_absent をストアに実装しストア経由でテスト」とのユーザー指示で**スコープ拡大＝create 競合も
  今実装**。create-only は `os.link`/`in` 判定でロック不要・安価。update CAS（既存値の lost-update 検知）と
  両方を **put 1 本＋`if_match`** で表現する（下記センチネル設計）。
- **backend 実装方針**（この計画で可能なことを確認済）:

  | backend | `if_match` 省略（LWW）| `if_match` 指定（update CAS）| head の情報源 |
  |---------|------|------|------|
  | local | temp+`os.replace`（原子）＋flock で直列化 | flock→`os.stat` で mtime_ns+size 比較→一致時 replace・不一致 `ConflictError` | `os.stat` |
  | S3 | `PutObject`（原子）| `PutObject(IfMatch=etag)`→412 を `ConflictError` | `HeadObject`（LastModified/ETag）|
  | dict | ロック下で値＋メタ更新 | メタの世代/サイズ比較 | **メタストア** |
  | NATS | `put`（原子）| `put`＋revision 比較（native 範囲は要調査・不足は loud に `NotImplementedError`）| `get_info` |

### create 競合（スコープ外・将来フェーズとして保持）

- create-only（既存なら失敗）を put で表すには `if_match` に**「不在を期待」を示す値**が要る
  （案: `if_match=ABSENT` センチネル / `head` が欠損時に返す「不在 FileInfo」を渡す）。
- backend native: local=`os.link`（dst 在れば `FileExistsError`）／S3=`IfNoneMatch="*"`（412）／
  dict=`key in` 判定／NATS=create セマンティクス。いずれもロック不要・原子。
- **今回は実装しない**。`if_match` のセンチネル設計が決まり次第、別フェーズで追加（update CAS と同一の
  conformancer 枠で「create 競合」観点を足すだけ）。

### conformancer scaffold の改修（旧 `put_if_absent` 前提からの追従）

- 既存 scaffold は旧設計（`put_if_absent` メソッド）で書かれている＝本実装時に**新 API へ書き換え**:
  - `assert_put_if_absent_concurrency_safe`（`tools/conformancer/__init__.py:190`）が叩く呼び出しを
    新 API に。**今回の MVP は update CAS** なので、まず **update 競合チェッカ**
    （同一 base `FileInfo` から 2 writer → 一方成功・他方 `ConflictError`・保存値=勝者）を正本にする。
  - 自己テスト `_ConditionalDict`（`tests/test_conformance.py:276`）も `put(if_match=...)` 形へ。
    自己テスト 2 本（safe 通過 / TOCTOU 落下）の二段構えは維持。
  - 実 backend テスト（`tests/test_conformance.py:324,331`・現 skip）は update CAS 版に直して skip 解除。
  - create 競合チェッカは**スコープ外フェーズで追加**（プレースホルダとして名前だけ計画に残す）。

## ゴール（要約）

同一キーへの並行 `put` で **lost update（先に書かれた更新が黙って消える）を検出**できるようにする。
現状 `put(key, value)` は **無条件 set（last-writer-wins）**。これを変えず、**opt-in の conditional put**
（条件付き書き込み）を足して、条件不一致は **fail-loud に失敗**させる。

## 現状把握（コードを読んで確定した事実）

- `put -> FileInfo`（`{filename,size}`）。revision/etag は **共通でないため core に載せない**（決定済・
  projectbrief / progress 意思決定の変遷）。`FileInfo` はこの最小契約を保つ。
- local の書き込みは `_LocalAtomicWriter`＝temp に全部書き → `os.replace(tmp, path)`。これは **torn-write
  防止（all-or-nothing）のみ**。`os.replace` は atomic だが **排他も lost-update 検出もしない**。
- 例外階層 `ManystoreError`（`exceptions.py`）＝`status`/`title`/`to_problem` で RFC 9457 に写る。既存
  サブクラス: `UnsafePathError`(400)/`ContextNotFound`(404)/`ReadOnlyContext`(403)/`NoSuchUpload`(404)。
- M043 で基底↔Protocol の lockstep を強制済＝**core Protocol に足すメソッドは async↔sync・基底・conformancer
  parity を全て揃える必要**。だが capability（別 Protocol・opt-in）なら core 表面を増やさず追加できる
  （旧 `SupportsPrefixListing` の前例）。
- ラッパ: `SafeKeyValueStore`/`ArrayKeyValueStore`/`AsyncToSyncKeyValueStore`/`RemoteKeyValueStore` が
  委譲伝播する。capability も同様に **対応 backend へ委譲・非対応は fail-loud** にする必要。

## 設計の核（5 つの判断）

### 判断1: capability ではなく **core 契約**（put を持つなら必須の最小挙動） 〔ユーザー確定 2026-06-27〕
- 並行安全な書き込みは **opt-in の能力ではなく、`put` を持つストアの必須挙動**。本製品は「最小機能」だが、
  **提供する挙動の最小保証（並行安全性）は担保**し、それを **conformancer（テストツール）が検証**する
  ＝「最小だが挙動は担保＋そのためのテストツール」という製品コンセプトそのもの。
- 線引きは **read-only か writable か の 1 点だけ**。read-only backend（http）は **`put` 自体を持たない**
  （既存の write op `put`/`delete`/`cp`/`mv` は `io.UnsupportedOperation` を raise）＝conditional 系も
  **同じく raise するだけ**で自明に除外される。**特別な capability Protocol も `isinstance` 分岐も要らない**。
- よってメソッドは **core 表面（`_StoreBase` の abstract）**に置く。M043 fail-loud で全 backend が実装を
  強制され、writable は native に並行安全実装・read-only は raise。「put はできるが conditional はできない」
  という**中間層を作らない**（ユーザー指摘）。当初の `@runtime_checkable` capability 案は**撤回**。
- prefix を capability→core 引数に畳んだ経緯（M030 廃止）とも整合: いずれも「中間の opt-in 層を作らず
  core 契約に寄せる」方向。prefix は全 backend が scan+filter で満たせ、conditional は read-only が put ごと
  持たないので writable のみ満たす——どちらも capability 不要。

### 判断2: MVP は **create-only（`put_if_absent`）から** 〔推奨・最小〕
- 最も安価で**全 backend で原子的に実現可能**な OCC＝「無ければ作る・有れば conflict」。version トークン
  不要（存在の有無だけ）。
  - **local**: `os.link(tmp, target)` は dst が在れば `FileExistsError`＝**atomic な create-only**
    （renameat2/ctypes 不要）。temp に書く→link→temp を unlink。
  - **S3**: `PutObject` に `IfNoneMatch="*"`（既存なら 412）。
  - **dict(memory)**: `if key in store: raise`（プロセス内なので単純に原子的）。
  - **NATS**: object store の create セマンティクス（**要確認**＝下記リスク）。
- ユースケースの大半（「このキーを確保する」「冪等 create」「ロック代わり」）をこれだけで満たす。
- API: `async def put_if_absent(self, key, value) -> FileInfo`（既存なら `ConflictError`）。

### 判断3: CAS（`put_if_match`）は **Phase 2**＝version トークンで 〔後回し〕
- 既存値の更新で OCC したいときに、**読んだ版と現在の版が一致するときだけ上書き**。
- **version は opaque な `str`**（呼ぶ側は中身を解釈しない）。backend native を畳む:
  - **S3** = ETag（`IfMatch=etag`・不一致は 412）。
  - **NATS** = revision/sequence（**要確認**）。
  - **local** = `mtime_ns`（+`size`）を合成した文字列（例 `f"{st.st_mtime_ns}-{st.st_size}"`）。
  - **dict** = キー毎の世代カウンタ。
- version の**読み口が要る**: `async def head(self, key) -> tuple[FileInfo, str]`（または
  `VersionedFileInfo`）で「現在値の version」を返す。`get` は値だけ・`head` は version 付きメタ。
- API: `async def put_if_match(self, key, value, version: str) -> FileInfo`（不一致は `ConflictError`）。
- **真の難所＝CAS の原子性**（mtime か etag かは本質でない）:
  - local の `put_if_match` は「version 読む→比較→replace」が **TOCTOU で racy**。原子的「変化してたら失敗
    する replace」syscall は無い。→ **target を flock/fcntl で囲って (read+compare+replace) を直列化**、
    または lockfile。S3/NATS は **サーバ側で atomic**（条件ヘッダ）＝ロック不要。
  - つまり mtime をトークンに採っても OK だが、local 更新は**ロックが必須**（create-only はロック不要）。

### 判断4: エラーは **fail-loud に raise**（error-as-value でない） 〔推奨〕
- `exceptions.py` に **`ConflictError(ManystoreError)`** を新設＝`status=409`/`title="Conflict"`。
  （HTTP 慣習では If-Match 不一致は 412 Precondition Failed・create 衝突は 409。まず 1 種 `ConflictError`
  で始め、必要なら `PreconditionFailedError(412)` を分ける。）
- M045（error-as-value の `put2`）とは**別系統**。conditional put は既存の raise ベース fail-loud に乗せる
  （put が raise する流儀と一致）。M045 を採るかは独立に判断。

### 判断5: async↔sync・ラッパ伝播・conformancer 強制
- core 契約なので **async/sync 両 Protocol（`AsyncKeyValueStore`/`SyncKeyValueStore`）に対で追加**し、
  `_StoreBase` で abstract 宣言＝M043 parity（基底↔Protocol・conformancer drift）を全部揃える。
- ラッパ（`Safe`/`Array`/`sync_bridge`/`Remote`）は **put と同様にそのまま委譲**（isinstance 分岐は不要＝
  下層が writable なら実装あり・read-only なら下層が raise を伝播）。
- **conformancer が並行安全性を強制**（製品コンセプトの核）= 「put を持つ全ストア」に対し並行 conditional
  put の property テスト（下記テスト戦略）。read-only は put が `UnsupportedOperation` を上げる時点で対象外。

## フェーズ分割

> ↓ 旧フェーズ表は**設計改訂（2026-06-28）で置換**。新 API（put 1 本＋`if_match`）に合わせた下表が正本。

| Phase | 内容 | 原子性 | 規模 |
|-------|------|--------|------|
| **P1（MVP）** | `head(key) -> FileInfo`（`modified_at` 追加・native token 内包）＋ `put(..., if_match=FileInfo)` の **update CAS**（不一致 `ConflictError`）。backend: local mtime+size+flock / S3 IfMatch / dict メタストア+世代。`if_match` 省略時は原子＋直列化の LWW | local は flock 必須・S3/NATS はサーバ側 | 中 |
| P2 | NATS の CAS 対応（revision・要調査／不足は loud に `NotImplementedError`）／serving 層（native REST・S3 GW If-Match）への配線 | — | 中 |
| **将来（スコープ外）** | create 競合（create-only）＝`if_match=ABSENT` センチネル設計 ＋ local `os.link`/S3 `IfNoneMatch`/dict `in`/NATS create ＋ conformancer の create 競合観点 | 全 backend native・ロック不要 | 小 |

> ユーザーの原体験は**既存値の並行上書き**＝更新 lost-update。よって **MVP は update CAS（P1）**。
> create 競合は安価だが今回スコープ外（将来フェーズに保持）。

## 未確定（ユーザー判断を仰ぐ点）

1. **スコープ＝必須挙動の範囲**: ユーザーの原体験は「**既存ファイルが並行 put で先勝ち上書き**される」＝
   *更新* の lost-update。これを満たすには **P2（`put_if_match` 更新 CAS）が必要**（create-only の
   `put_if_absent` だけでは既存上書きを守れない）。→ **P1+P2 を MVP に含める**のが筋（推奨）。
   `put_if_absent` は安価な create 専用として併設。**この 1 点だけ確定すれば実装に入れる**。
2. **version の読み口**: `head(key) -> (FileInfo, version)`（別メソッド・推奨＝FileInfo 不変を保つ）か、
   `VersionedFileInfo`（FileInfo 拡張）か。
3. **エラーの粒度**: `ConflictError(409)` 1 種か、create 衝突=409 / If-Match 不一致=412 を分けるか。
4. **API 形**: 専用メソッド（`put_if_absent`/`put_if_match` を core 契約に）か、`put(..., if_match=)` の
   キーワード拡張か。**専用メソッド推奨**（無条件 `put` の単純な契約を汚さない・read-only は put 同様 raise）。
5. **NATS**: object store が create-only / revision CAS をどこまで native に持つか（要コード調査）。
   無ければ NATS の writable conditional は **その時点で実装可能な範囲＋足りない分は loud に
   `NotImplementedError`**（黙って last-writer-wins に落とさない＝要求7）。

## 実装状況（2026-06-27）

- **スコープ確定**（ユーザー承認）: `put_if_absent`(create CAS)＋`put_if_match`(update CAS) を **core 必須挙動**。
  version=opaque str（S3 etag/NATS revision/local mtime_ns+size）、読み口 `head`、不一致は `ConflictError`。
- **例外集約済**（M048）: `exceptions.py` に `UnsupportedOperation`(405)/`ConflictError`(409)。read-only は
  conditional 系も `UnsupportedOperation` を上げる（put と同じ＝capability 不要）。
- **並行安全性チェッカ scaffold 済**: `conformancer.assert_put_if_absent_concurrency_safe`。**設計改訂
  （ユーザー指摘）**＝50 並列「1 つ勝つ」は *何を確認するか* が曖昧 → **2 writer・内容を変え（A/B）・大きめ
  size・後発を stagger 秒ずらす**方式へ。検証する不変条件＝①ちょうど一方成功・他方 `ConflictError`
  ②**保存値=勝者の内容**（敗者上書き・torn write を排除）。戻り値＝勝者 content で**どちらが優先されたか
  識別可**。**自己テスト 2 本 active**（safe→先行 A が勝つ / TOCTOU を `stagger=0` で重ねる→両方成功を
  AssertionError 検出）。**実 backend テストは `@pytest.mark.skip`**（M046 本実装で解禁）。
  put_if_match も同型（同一 base version の 2 writer・一方成功・保存値=勝者）で後日。
- **MVP 実装完了（2026-06-28）**: put 1 本＋`if_match`（None/ABSENT/FileInfo）＋`head` を実装。
  protocols（`_Absent`/`ABSENT`/`type IfMatch`・FileInfo に modified_at/etag・put 全署名変更・head 既定実装）／
  dict メタストア（`_seq`→etag・ABA 安全）／local（os.link create・flock+stat update CAS・os.stat head）／
  S3（IfNoneMatch/IfMatch・HeadObject）／NATS（head のみ・CAS は loud）／http（read-only・HEAD head）／
  全 wrapper 委譲。conformancer は実ストア経由（Dict/Local）で create+update CAS を強制、否定テストのみ
  故意 TOCTOU ダブル。`make check` 緑（fast 139）。**残＝M046残**（NATS CAS / serving 配線 / remote）。

## テスト戦略（conformancer が並行安全性を強制＝製品コンセプトの核）

- **並行 property テスト（必須挙動の担保）**: `put` を持つ全ストアに対し、
  - `asyncio.gather` で同一キーへ **N 本の `put_if_absent`** → **成功はちょうど 1・残りは `ConflictError`**。
  - 同一 base version から **N 本の `put_if_match`** → **成功ちょうど 1・残り `ConflictError`**（lost-update を
    黙って通さない）。一致時のみ version が進む。
  - これを conformancer の新観点（`FileStoreTester` の並行系 run、または専用 `assert_concurrent_safe`）に。
    非原子実装（exists→put の TOCTOU）は高確率で落ちるよう**反復・多重度を上げて**検出力を稼ぐ
    （※並行バグは確率的＝決定的検出は不可。best-effort である旨を明記）。
- 単体: 2 連続 `put_if_absent` の 2 回目が `ConflictError`（local/dict/S3 fake）。read→他者書込→
  `put_if_match(old_version)` が `ConflictError`。local は `os.link`（create）/ flock（update）で直列化を確認。
- read-only（http）: conditional 系も `put` 同様 `UnsupportedOperation`/`NotImplementedError`（fail-loud 伝播）。

## スコープ境界（最小・汎用を壊さない）

- conditional put は **core 契約**（`AsyncKeyValueStore`/`SyncKeyValueStore` に追加・`_StoreBase` で abstract）。
  ただし **`put`/`get`/`iter` 等 既存メソッドの挙動は不変**（無条件 `put` は last-writer-wins のまま）。
- `FileInfo` は不変（version を混ぜない）。version は `head`/conditional の戻り値でのみ露出。
- request/response 封筒・pub/sub は非ターゲットのまま（conditional put はストレージ操作＝in-scope）。
- 「最小機能だが、提供する挙動の最小保証（並行安全性）は担保し conformancer で検証する」を死守。
