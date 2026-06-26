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

### 判断1: core ではなく **capability**（opt-in Protocol）にする 〔推奨〕
- conditional put は **全 backend が支えられない**（http は read-only・実装差が大）。core Protocol に
  載せると M043 lockstep で全 backend に強制＝最小-core 違反。
- `@runtime_checkable` な **`SupportsConditionalPut` capability Protocol** として定義し、対応 backend だけ
  実装。非対応は「メソッドが無い」＝呼ぶ側が `isinstance(store, SupportsConditionalPut)` で分岐、または
  ラッパが **fail-loud に `NotImplementedError`**（暗黙フォールバック無し＝要求7）。
- prefix を capability から **core 引数に畳んだ**経緯（M030→廃止）と矛盾しない: prefix は「全 backend が
  scan+filter で必ず支えられる」ので core 化できた。conditional put は **支えられない backend がある**ので
  capability が正しい（線引き＝「全 backend が満たせるか」）。

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

### 判断5: async↔sync・ラッパ伝播
- capability も **async/sync 両 Protocol** を対で定義（`SupportsConditionalPut` / `SupportsConditionalPutSync`）。
- `SafeKeyValueStore`/`ArrayKeyValueStore`/`sync_bridge`/`RemoteKeyValueStore` は **下層が capability を
  満たすときだけ委譲**（`isinstance` 判定）し、満たさないなら `NotImplementedError`（fail-loud）。
- conformancer に capability の存在チェック＋（Phase 2 なら）CAS 挙動テストを足す。

## フェーズ分割

| Phase | 内容 | 原子性 | 規模 |
|-------|------|--------|------|
| **P1（MVP）** | `put_if_absent`（create-only）＋ `ConflictError` ＋ local(`os.link`)/dict/S3(`IfNoneMatch`) | 全 backend native・**ロック不要** | 小 |
| P2 | `head`(version 読み口) ＋ `put_if_match`（CAS）＝S3 etag / dict 世代 / local mtime+size+flock | local は flock 必須・S3/NATS はサーバ側 | 中 |
| P3 | NATS の create/CAS 対応（要調査の結果しだい）／serving 層（native REST・S3 GW If-Match）への配線 | — | 中 |

> P1 だけでも「キー確保・冪等 create・lost-update の一部（新規衝突）」を fail-loud に解決できる。
> ユーザーの原体験（並行 put で先勝ち）に **更新 CAS まで要るなら P2 まで**。

## 未確定（ユーザー判断を仰ぐ点）

1. **スコープ＝どこまで今やるか**: P1（create-only）だけ MVP で出すか、P2（更新 CAS）まで一気にやるか。
   推奨は **P1 を MVP**→効用を見て P2。ユーザーの主訴が「既存値の並行更新」なら P2 まで必要。
2. **version の読み口**: `head(key) -> (FileInfo, version)` か `VersionedFileInfo`（FileInfo 拡張型）か。
   FileInfo 本体は不変に保ちたい（決定済）ので **別メソッド `head` 推奨**。
3. **エラーの粒度**: `ConflictError(409)` 1 種か、create=409 / If-Match=412 を分けるか。
4. **API 形**: capability メソッド（`put_if_absent`/`put_if_match`）か、`put(..., if_match=/if_absent=)` の
   キーワード拡張か。capability メソッド推奨（core put の契約を汚さない・isinstance で能力検出可）。
5. **NATS**: object store が create-only / revision CAS をどこまで native に持つか（要コード調査）。
   無ければ NATS は capability 非対応（fail-loud）で割り切る。

## テスト戦略

- P1: 同一キー 2 連続 `put_if_absent`＝2 回目が `ConflictError`（local/dict/S3 fake）。並行性は
  `asyncio.gather` で 2 本走らせ「成功 1・Conflict 1」を確認（local は `os.link` の atomic 性に依存）。
- P2: read→他者書き込み→`put_if_match(old_version)` が `ConflictError`（CAS 不一致）。一致時は成功して
  version が進む。local は flock で直列化されることを確認。
- capability 非対応 backend（http）でラッパ経由が **fail-loud**（`NotImplementedError`）。
- conformancer: capability メソッド存在チェック＋（P2）CAS 観点を `FileStoreTester` の延長に。

## スコープ境界（最小・汎用を壊さない）

- core Protocol（`AsyncKeyValueStore`/`FileStore`）は**不変**。conditional put は **別 capability Protocol**。
- `FileInfo` は不変（version を混ぜない）。version は capability の `head`/戻り値でのみ露出。
- request/response 封筒・pub/sub は非ターゲットのまま（conditional put はストレージ操作＝in-scope）。
