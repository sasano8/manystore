# M078 設計：Store の上に載る「合成層」— op-middleware / codec / auth（doc-first）

> ステータス: **doc-first（設計のみ・未実装）**。2026-07-03 起案。M071（公開 1 Store 統合）完了を前提。
> 本書は 3 系統を 1 本にまとめた設計の俯瞰。実装は系統ごとに別マイルストンへ割る（下記「割り方」）。

## 目標像（To-Be）

M071 で公開は **1 つの Store**（`AsyncStore` ＝値 API `put/get/…` ＋ IO API `open_reader/open_writer`）に
畳んだ。その上に、継承やラッパ乱立ではなく**明示的に順序づけた合成**で横断関心を載せたい
（productContext「ネスト禁止＝挙動が割れる」の健全な代替）。ただし「横断関心」と一括りにした 3 つは
**shape（形）が違う**ので、1 つの機構に押し込めない。

| 系統 | 性質 | 触るもの | 合成の形 | 既存資産 |
|---|---|---|---|---|
| **(1) op-middleware** | 呼び出しを挟む・**意味保存** | 呼び出し（メソッド境界） | per-method フック or op 封筒 | M014(retry)/M015(logging) |
| **(2) codec** | **値そのものを変換**（対称: write=encode / read=decode） | バイト（値 ＋ FileObject） | 値経路と IO 経路の両方を覆う Store ラッパ | `manystore/crypto.py` |
| **(3) auth** | **per-call のコンテキスト**（トークン/principal）を差す | 呼び出しの文脈 | 束縛 view or ambient context | — |

**核心**：(1) は「呼び出しを包む」だけ、(2) は「中身のバイトを変える」、(3) は「呼び出しに文脈を差す」。
これらを 1 本の op 封筒に載せると、codec は content-awareness を汎用フックへ漏らし、auth は最小 IF に無い
context 引数を要求する（型安全 or 最小性を失う）。**→ 別レイヤに割る**。

---

## (1) op-middleware（logging / retry / metrics / cache / tracing）

呼び出しを前後で挟むだけで**戻り値の意味を変えない**関心。ここが本来の M078。

- **形の二択**:
  - **per-method フック**（薄い）: `before(op, args)` / `after(op, result)` / `on_error` を Store の各メソッドが呼ぶ。
    関心が少なく型安全。ただし ~15 メソッド分の配線が要る（基底に 1 回書けば済む）。
  - **op 封筒**（汎用）: `dispatch(op_name, call)` に全メソッドを通す。ミドルウェアは 1 本の関数で全 op を挟める
    （ASGI 風）。ただし引数/戻り値が `op_name` で分岐＝**型安全を失う**。
  - **私見**: 関心が logging/retry/metrics/cache 程度なら **per-method フックを基底 1 か所**に実装し、
    ミドルウェアは `MiddlewareStore(inner, [mw1, mw2, …])` で順序合成。op 封筒は「任意 op を書き換える」需要が
    出てから（YAGNI）。
- **対象外（線引き）**: **Safe（必須パス検証）** と **基底の buffered↔stream 合成** はミドルウェア化しない
  （前者は opt-out 不可の安全不変条件・後者は型の一部）。
- **順序**: 外→内で `[logging, tracing, metrics, retry, cache] → backend`。retry は cache の内側（キャッシュヒットは
  リトライ不要）、metrics は retry の外側（総所要を測る）か内側（1 試行を測る）で意味が変わる＝**順序が仕様**。
- 包含: **M014（op-retry）/ M015（logging）はこの層で実現**。

---

## (2) codec（エンコーダ・デコーダ層＝json / 暗号化 / 圧縮）

**値そのもの**を write で encode / read で decode する対称変換。op-middleware と決定的に違うのは
「**バイトを変える**」点と「**値経路（put/get）と IO 経路（open_reader/open_writer）の両方**を覆う」点。

### 既存前例（土台）
`manystore/crypto.py` は **FileObject 境界の codec** を既に持つ:
- `StreamCipher.transform(offset, data)`（チャンク境界非依存の対称バイト変換）
- `CipherReader` / `CipherWriter`（`open_*` の戻り AsyncFileObject を 1 枚包む）
現状は**利用側が open_* の戻りを手で包む**設計（store 本体不変＝「真の性能は client wrap」原則）。

### 設計案：codec を Store ラッパへ昇格
`CodecStore(inner, codec)` を用意し、**両経路を 1 つの codec で覆う**:
- `put(k, v)` → `inner.put(k, codec.encode(v))`／`get_or_raise(k)` → `codec.decode(inner.get_or_raise(k))`
- `open_writer(k)` → `codec.wrap_writer(inner.open_writer(k))`（= 既存 `CipherWriter` 相当）
- `open_reader(k)` → `codec.wrap_reader(inner.open_reader(k))`（= 既存 `CipherReader` 相当）
- codec Protocol は `encode(bytes)->bytes` / `decode(bytes)->bytes` ＋ streaming 用 `wrap_reader/wrap_writer`。
  ストリーム変換が offset 非依存でない codec（例: 全体 gzip）は **IO 経路を buffer 化**するしかない
  （= codec が「この codec は streaming 可否」を宣言）。暗号（XOR/CTR 系）は offset 可変＝真の streaming 可。

### codec が既存契約に触れる論点（重要・要判断）
- **`FileInfo.size` の意味**: 変換で長さが変わる codec（圧縮）だと、`head()` が返すのは**保存後（暗号文/圧縮後）の
  size**であって論理 size ではない。暗号（同長）は不変だが圧縮は不一致＝「size は物理量」と割り切るか、codec が
  論理 size を別途持つか。**要決定**。
- **sha256 メタ（M013）/ Verify（M067）**: 保存される content hash は**暗号文のハッシュ**。download 整合性検証は
  暗号文を検証する（平文の完全性ではない）。これで良いか＝**要決定**（多くの場合 OK：保存物の完全性を見る）。
- **CAS（if_match の ETag）**: ETag は保存バイト（暗号文）由来。楽観並行制御としては機能するが、利用側は
  自分の平文 view で考える＝説明の一貫性に注意。
- **metrics との順序**: metrics を codec の**外**に置けば平文 size、**内**に置けば暗号文 size を測る＝順序が仕様。
- **cache との順序**: cache を codec の外（平文キャッシュ＝復号の再実行を省くがメモリに平文）／内（暗号文キャッシュ）で
  トレードオフが変わる。
- **Safe との関係**: Safe は**キー**を検証、codec は**値**を変換＝直交。順序は Safe 外側で問題ない。

---

## (3) 認証認可層（auth）

2 つの別物を含む。**authn（誰であるか／資格情報）** と **authz（何を許すか／ポリシー）**。

### authn — 資格情報の流し方（本丸 = per-call context）
- **2 モードの切替**:
  - **passthrough**: 呼び出し側トークンを backend へ透過（`Authorization` 等）。**remote backend でのみ意味を持つ**
    （RemoteStore / HTTP / S3 STS 等）。local / in-proc は透過先が無い＝no-op。
  - **store-creds**: ストアに定義された資格情報をそのまま使う（現行の boto/config 委任＝既定）。
- **per-call トークンをどう流すか**（現行の最小 IF はメソッドに context 引数を持たない）:
  - (a) **`store.with_auth(token)` で束縛 view を返す**（**推し**）: 最小 IF 非破壊。RemoteStore なら
    `Authorization` ヘッダを束ねた薄い view を返すだけ。呼び出しは通常どおり `view.put(...)`。
  - (b) **contextvars で ambient**: 深い呼び出しスタックで token を引き回さずに済むが magical。テスト/並行で罠。
  - (c) **context 引数を全メソッドに追加**: 最小 IF 破壊＝**YAGNI で却下寄り**。
- **backend 側の受け口**: passthrough を効かせるには remote 系 backend が「per-view の token 上書き」を受ける必要。
  束縛 view (a) なら backend を再構築せず header だけ差し替える薄い実装で済む。

### authz — ポリシー（allow/deny）: **スコープ判断が要る**
- authz は「(principal, op, key) を許すか」の**ポリシー**で、backend に対応物が無い（S3 IAM 等はあるが manystore の
  抽象外）。**app 固有性が非常に高い**。
- **projectbrief「最小・汎用に保つ／利用側固有の要求は取り込まない」との整合**: **authz（ポリシー）は manystore の
  スコープ外＝利用側 adapter に閉じる**のが素直、というのが私見。manystore が持つのは **authn の機械的な透過**まで。
- **local の認可**: OS 権限に per-key の概念が無い。やるなら「ミドルウェアがポリシーを in-proc 判定して delegate 前に
  弾く」形しかない＝これは上記 authz と同じく**スコープ外候補**。**要 product 判断**。

---

## 3 層の合成順（案）

外→内で:

```
auth(view 束縛) → op-middleware(logging/tracing/metrics/retry/cache) → codec(encode/decode) → Store(backend)
```

- auth は最外（誰の資格情報でこの一連を回すかを最初に確定）。
- codec は最内寄り（backend の直前で暗号文/圧縮に変換＝backend には常に変換後が渡る）。
- ただし metrics/cache と codec の相対順は「平文/暗号文どちらを測る・蓄える」かで**利用側が選ぶ**＝順序を仕様として露出。

---

## 割り方（マイルストン）

- **M078**: op-middleware（per-method フック基底 ＋ `MiddlewareStore` 合成）。M014/M015 を内包。**まずここ**。
- **M078b（codec）**: `CodecStore` ＋ codec Protocol。`crypto.py` を土台に「暗号化 Store」を最初の成果物に。
  size/sha256/CAS の論点を決める。
- **M078c（auth）**: `with_auth` 束縛 view（authn passthrough / store-creds 切替）。**authz はスコープ外の可能性が高い**
  ＝product 判断を先に取る。

いずれも **M071 の 1-Store の上に載る合成**で、backend にも Safe にも触れない。

---

## 未決（要ユーザー/ product 判断）

1. **op-middleware の形**: per-method フック（推し・薄い）か op 封筒（汎用・型安全喪失）か。
2. **codec の size 意味**: `FileInfo.size` は物理（変換後）で割り切るか、論理 size を別途持つか。
3. **codec × 整合性メタ**: 暗号文のハッシュ/ETag を「保存物の完全性」として受け入れるか。
4. **auth の per-call 機構**: `with_auth` 束縛 view（推し）／contextvars／却下の context 引数、どれを正とするか。
5. **authz はスコープ内か**: ポリシー（allow/deny・local 認可含む）を manystore に入れるか、利用側 adapter に閉じるか
   （私見＝閉じる＝スコープ外。authn passthrough までが本体の責務）。

## 確定（起案時点・私見ベース／未合意）

- 3 系統は 1 機構に載せず**別レイヤ**に割る（shape 差＝呼び出し挟み／バイト変換／文脈差し）。
- codec は既存 `crypto.py` の FileObject codec を土台に、値経路にも自動適用する Store ラッパへ昇格。
- auth は最小 IF 非破壊の**束縛 view**（`with_auth`）を第一候補、authz はスコープ外候補。
