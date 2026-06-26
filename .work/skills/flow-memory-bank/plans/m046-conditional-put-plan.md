# M046: put の並行更新（conditional put / lost-update 検出）設計計画（deep think・着手前ゲート）

> 指示の正本: ユーザー対話（2026-06-27）＝「atomic write は排他していない／先勝ちが起きる、失敗すべき」。
> 合意済の方針: **失敗判定は mtime 単独でなく version/etag の compare-and-swap（CAS）**。local では mtime(+size)
> を etag 的トークンに使ってよい（modern FS は ns 精度）。本ファイルは **doc-first の設計成果物**。実装はしない
> ＝設計と段階計画・未確定点の確定まで。命名は M021/M025 plan の慣習に倣う。

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

| Phase | 内容 | 原子性 | 規模 |
|-------|------|--------|------|
| **P1（MVP）** | `put_if_absent`（create-only）＋ `ConflictError` ＋ local(`os.link`)/dict/S3(`IfNoneMatch`) | 全 backend native・**ロック不要** | 小 |
| P2 | `head`(version 読み口) ＋ `put_if_match`（CAS）＝S3 etag / dict 世代 / local mtime+size+flock | local は flock 必須・S3/NATS はサーバ側 | 中 |
| P3 | NATS の create/CAS 対応（要調査の結果しだい）／serving 層（native REST・S3 GW If-Match）への配線 | — | 中 |

> ユーザーの原体験は**既存値の並行上書き**＝更新 lost-update なので、**MVP は P1+P2**（create と update の
> 両 CAS）が筋。P1 単独は「キー確保・冪等 create」のみで既存上書きは守れない。P3 は後追い。

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
