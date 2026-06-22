# M021: S3 ゲートウェイ + backend=s3 パススルー 設計計画（deep think・着手前ゲート）

> 指示の正本: `interrupt/20260623-s3-gateway-and-passthrough.md`（supervisor, priority normal）。
> 本ファイルは **着手前 deep think（設計パス）の成果物**。実装はしない＝設計と段階計画の確定まで。
> 命名は M019（`m019-ui-plan.md`）の慣習に倣う。M020 まで使用済みのため **M021** を採番。

## ゴール（指示の要約）

manystore を **S3 互換 API のサーバ（ゲートウェイ）として公開**する。クライアントは S3
プロトコルで話し、背後で任意の manystore backend（local / nats / s3）へ読み書きが向く。
加えて **backend がそのまま s3 のときはパススルーモード**（バイトを manystore に通さず、可能なら
presigned URL リダイレクトで実 S3 へ直送。不可ならプロキシにフォールバック）を optional で持つ。

スコープ境界（最重要）: ストレージ抽象の**最小・汎用を壊さない**。ゲートウェイは
`KeyValueStore` IF の上に**薄く乗せる別層**であり、コア（`async_storage` / `backends` / `safe_path`）は
**不変**に保つ。projectbrief「最小・汎用に保つ／独立ライブラリを肥大させない」を死守する。

## 現状把握（コードを読んで確定した事実）

- **共通 IF**: `KeyValueStore`（put/get/iter/list/exists/delete/cp/mv/connect/aclose）と
  `FileStore`（open_reader/open_writer→`FileObject`）。`backends/__init__.create_key_value_store(backend, ...)`
  が入口。`SafeKeyValueStore` が key を `validate_safe_path` で検証して委譲（`..`・先頭`/`・`\`・NUL を拒否）。
- **既に server 層がある**（M019）: `implement/`（HTTP 非依存・`StorageService` が protocol→`KeyValueStore`
  写像、`SafeKeyValueStore` でキー検証、`PollingWatcher`）＋ `server/`（FastAPI・遅延 import・extra
  `manystore[server]`）＋ `client/`。**ゲートウェイはこの実績パターンの「別フロント protocol」版**として作れる。
- **S3 backend**: `S3KeyValueStore`/`S3FileStore` が bucket/endpoint/region/credentials/addressing_style を保持し、
  毎オペ aiobotocore クライアントを生成。presigned URL 生成は今は無いが aiobotocore client に
  `generate_presigned_url` があるので **S3 backend を拡張すれば取れる**（パススルーの鍵）。
- **検証**: `make check`（ruff format-check + pytest）。実 backend は `make e2e-up`（SeaweedFS に dev identity）。
  現状 59 passed, 1 skipped。

重要な含意:
1. ゲートウェイは **既存 `StorageService` をほぼそのまま再利用**でき、変えるのは「前段の HTTP protocol」だけ
   （manystore REST → S3 XML/REST）。サービス中核を 2 度書かない。
2. **パススルーは generic `KeyValueStore` では表現できない**（presigned URL は backend 固有能力）。
   →「パススルー可能 backend」を表す**狭い optional capability**（後述 `S3Passthrough`）を S3 backend にだけ生やす。
   ゲートウェイは `isinstance`/duck-typing で能力を検出し、無ければプロキシにフォールバック＝コア IF は汚さない。

---

## 論点1: 対応する S3 操作の最小集合（YAGNI）

### 比較した選択肢

| 案 | 含む操作 | 評価 |
|----|----------|------|
| A 最小 | GET / PUT / HEAD / DELETE / ListObjectsV2 | KVS と 1:1。**watch/notify は S3 protocol に無い**ので UI 連携は別系統。**推奨** |
| B 最小+multipart | A + CreateMultipartUpload/UploadPart/Complete/Abort | 大容量 PUT に必要。`S3FileStore` の multipart writer が既にある。**段階2へ繰越** |
| C フル | A + B + ACL/タグ/バージョン/バケット作成 等 | 抽象に無い概念多数＝コア汚染。**却下** |

### 確定（推奨 = 案 A を必須、B を段階2 optional）

必須の最小集合と IF マッピング:

| S3 操作 | HTTP | manystore IF へのマップ |
|---------|------|------------------------|
| GetObject | `GET /{bucket}/{key}` | `service.get(ctx, key)` → 200 + body（404→404 + S3 XML エラー） |
| PutObject | `PUT /{bucket}/{key}` | `service.put(ctx, key, body)` → 200 + ETag ヘッダ |
| HeadObject | `HEAD /{bucket}/{key}` | `service.exists` + size → 200/404（Content-Length 等メタ） |
| DeleteObject | `DELETE /{bucket}/{key}` | `service.delete(ctx, key)` → 204 |
| ListObjectsV2 | `GET /{bucket}?list-type=2&prefix=&delimiter=` | `service.list_entries(ctx, prefix)` → S3 XML（Contents/CommonPrefixes） |

- **bucket = manystore の context**（既存 config の `[contexts.<name>]` をそのまま S3 バケット名として公開）。
  →「複数 backend を 1 ゲートウェイで」が config だけで成立し、UI 版と設定資産を共有できる。
- **delimiter（`/`）対応**は ListObjectsV2 で擬似ディレクトリを返すため最小集合に含める（`iter()` を
  prefix 絞り→`delimiter` で CommonPrefixes に畳む。`service.list_entries` を拡張 or gateway 側で畳む）。
- **却下**: multipart（必須では大容量を諦め単発 PUT のみ）/ presigned 署名検証以外の AWS 署名検証
  （論点2参照）/ バケット CRUD / ACL・タグ・バージョニング（抽象に無い＝YAGNI）。

未決: ListObjectsV2 の **continuation token / MaxKeys ページング**を最小に含めるか
（現状 `list_entries` は limit のみで token 無し。大量キーで切れる）。→ 暫定 MaxKeys 上限で打ち切り、
本格ページングは backend の M012（pagination）と合流させる方が筋。**ユーザー判断項目**。

---

## 論点2: パススルー（backend=s3）とクレデンシャル/署名の橋渡し

### 比較した選択肢（パススルー実現手段）

| 案 | 仕組み | 長所 | 短所/リスク |
|----|--------|------|-------------|
| P1 presigned redirect | gateway が実 S3 の presigned URL を作り **307/302 でクライアントへ返す**。クライアントが直接 S3 と通信 | データ平面が gateway を通らない（要件そのもの）。実装が薄い | クライアントが**リダイレクト追従**必要。署名は **gateway 保有の実 S3 資格情報**で作る（クライアント資格情報は使わない） |
| P2 透過署名プロキシ | クライアントの SigV4 をそのまま実 S3 へ中継（または再署名）しバイトも中継 | 完全な S3 互換 | データ平面が gateway を通る＝パススルーの趣旨に反する。**フォールバック専用** |
| P3 STS/AssumeRole 連携 | クライアント資格情報→実 S3 の一時資格に橋渡し | マルチテナントで正しい権限分離 | 大掛かり・S3 互換サーバ（SeaweedFS）で STS 非対応も。**YAGNI＝却下** |

### 確定（推奨）

**パススルー本線 = P1（presigned redirect）、フォールバック = P2（プロキシ中継）**。

- **クレデンシャルの扱い（結論）**: パススルーの presigned URL は **gateway が設定で保持する実 S3 資格情報
  （= s3 backend の access_key/secret_key）で署名する**。**クライアントの資格情報は実 S3 署名に転用しない**
  （転用は STS 等が要り YAGNI、かつ S3 互換サーバで非現実的）。クライアント↔gateway 間の認証は
  **gateway 自身の認証層**（既定 localhost / 任意 token。M019 と同方針）で行い、実 S3 への署名とは分離する。
  → 「クライアント資格情報→実 S3 署名の橋渡し」は **やらない**。gateway が信頼境界となり自分の資格で代理署名する。
  これは独立ライブラリの最小性に合致し、マルチテナント権限分離が要るなら利用側 adapter の責務（スコープ外）。

- **パススルー可否の境界（P1→P2 フォールバック条件）**:
  - presigned URL を作れない/作っても**到達不能**（クライアントから実 S3 エンドポイントへ直接届かない網・
    内部エンドポイント）→ P2 プロキシ。
  - **署名条件が合わない**（クライアントが指定するヘッダ・content-type・レンジが presigned の条件に乗らない、
    POST フォーム等 presigned で表せない要求）→ P2。
  - addressing_style 不一致でクライアントが URL を解釈できない懸念 → 既定は素直に P2 を選べる**設定スイッチ**を持つ
    （`passthrough = "redirect" | "proxy" | "off"`。既定は安全側の `proxy` か、明示 opt-in の `redirect`）。

- **能力検出（IF を汚さない実装）**: コア `KeyValueStore` には presigned を足さない。代わりに
  **optional capability protocol** を `backends/s3.py` 側に置く:

  ```python
  class SupportsPresign(Protocol):
      async def presign_get(self, key: str, *, expires: int = 900) -> str: ...
      async def presign_put(self, key: str, *, expires: int = 900) -> str: ...
  ```

  `S3KeyValueStore` にだけこれを実装（aiobotocore `generate_presigned_url`）。gateway は
  `isinstance(store, SupportsPresign)`（or duck-typing）で**パススルー可能か**を判定し、不可なら通常の
  `service.get/put`（＝プロキシ）に落ちる。**他 backend・コア IF は一切変更しない**。

未決（ユーザー判断）:
- `passthrough` の**既定値**を `proxy`（安全・常に動く）にするか `redirect`（要件の直送を既定で体感）にするか。
- presigned の**既定 TTL**（暫定 900 秒）。
- クライアント↔gateway の S3 SigV4 署名検証を**するか**（する＝本物の S3 互換、しない＝gateway 認証に委ねる）。
  最小は「**SigV4 は検証せず gateway 認証に委ねる**」を推奨（実装が軽く、独立ライブラリの範囲に収まる）。

---

## 論点3: 置き場・スコープ（サーバ実装の依存と配置）

### 比較した選択肢

| 案 | 配置 | 評価 |
|----|------|------|
| S1 | `manystore.server` に S3 ルートを相乗り | 既存 UI サーバと混在＝関心が混ざる。却下 |
| S2 | 新サブパッケージ `manystore.gateway`（implement 再利用・FastAPI 遅延 import・既存 extra `[server]` を流用 or 新 extra `[gateway]`） | M019 の実績パターンと同型。**推奨** |
| S3 | 別ディストリビューション/別リポ | M019 で「単一ディストリ＋extras」に巻き戻した判断と矛盾。却下 |

### 確定（推奨 = 案 S2）

```
manystore/
  implement/        # 既存（不変・再利用）。StorageService が protocol→KeyValueStore 写像 + Safe + watcher
    s3map.py        #   (新) S3 操作 ⇄ KVS 操作 + S3 XML シリアライズ + ListObjectsV2 の delimiter 畳み（HTTP非依存）
  gateway/          # (新) S3 互換フロント。fastapi を遅延 import。
    app.py          #   create_s3_app(service): FastAPI app（lifespan で service.connect/aclose）
    routes.py       #   GET/PUT/HEAD/DELETE/{bucket}/{key} + GET /{bucket}?list-type=2、S3 XML エラー
    passthrough.py  #   SupportsPresign 検出 → redirect / proxy フォールバックの分岐
    __main__.py     #   python -m manystore.gateway --config <toml> [--passthrough redirect|proxy|off]
  backends/s3.py    # (拡張) SupportsPresign（presign_get/presign_put）を S3KeyValueStore に追加。**コア IF は不変**
```

- **依存（結論）**: サーバは **FastAPI + uvicorn**（既存 `[server]` extra と一致＝新規依存ゼロ）。
  S3 XML は**外部依存を足さず stdlib `xml.etree.ElementTree`** で組む（ListObjectsV2/エラー応答は単純な XML）。
  → 新しい重い依存を入れない＝肥大回避。extra は **`[server]` を流用**（gateway も同じ fastapi/uvicorn）。
  必要なら `gateway = ["fastapi","uvicorn"]` を別名で切るが、初期は `[server]` 相乗りで十分（YAGNI）。
- **コアへの侵襲は s3.py の presign 追加のみ**。`async_storage` / `safe_path` / `local`/`nats`/`http` backend は不変。
- **キー制約の注意**: gateway は `service` 経由＝`SafeKeyValueStore` が `..`・先頭`/`・`\`・NUL を弾く。
  S3 のキーはこれらを含み得る（特に先頭以外の任意文字）。**ゲートウェイが受ける S3 キーの集合は
  `validate_safe_path` が許す範囲に制限される**＝弾かれたキーは S3 エラー（400/`InvalidArgument` 等）にマップする。
  これはセキュリティ上望ましい制約として**意図的に維持**する（緩めない）。未決: パススルー直送時は
  実 S3 のキー制約に従う（gateway を通らないため検証が効かない）点を仕様として明記するか。

---

## 論点4: 段階計画（コミット単位）

> 各ステップ末で flow の開発内ループ（自己点検 = flow→[[unit-quality]]）を回し、`make check` 緑で 1 コミット。
> 実装は本 deep think 確定後に別セッションで着手（本タスクでは**実装しない**）。

- **S0（設計確定）**: 本ファイル作成 + activeContext/progress 追記。**完了（2026-06-23・前サイクル）**。
- **S1 gateway 本体（必須操作）**: ✅ **完了（2026-06-23）**。`implement/s3map.py`（delimiter 畳み + S3 XML +
  エラー XML、HTTP 非依存）＋ `gateway/{__init__,app,routes,__main__}.py`。GET/PUT/HEAD/DELETE/
  ListObjectsV2(prefix+delimiter) を `StorageService` 上に 1:1 実装。bucket=context、ETag=MD5、例外→S3
  エラー XML（NoSuchKey/NoSuchBucket/AccessDenied/InvalidArgument）。S3 XML は stdlib ElementTree（新依存
  ゼロ）、FastAPI 遅延 import・`[server]` extra 流用、コア IF 不変。SigV4 検証なし（Q2 採用）。
  ListObjectsV2 のページングは max-keys 上限 1000 でクランプ・打ち切りのみ（continuation token は**繰延**＝Q3）。
  - 実装ファイル: `manystore/gateway/__init__.py`・`app.py`・`routes.py`・`__main__.py`、`manystore/implement/s3map.py`。
    テスト: `tests/ui/test_gateway.py`(8 ケース・local backend で CRUD+LIST+エラー)・`tests/ui/test_s3map.py`(5・純ロジック)。
  - 受け入れ: ✅ `make check` 緑（**76 passed, 1 skipped**）。✅ **実 S3 クライアント往復で S1 を検証済み**
    （2026-06-23 後続サイクル）。当初「実 client 疎通は S4 へ繰越（同期 boto3 は新依存）」としたが、**aiobotocore は
    botocore を内包する実 S3 クライアント**なので `endpoint_url=<起動した gateway>` に向ければ**新依存ゼロ**で往復可能と
    判明＝前倒し解消。`tests/ui/test_gateway_s3client.py`(4) で uvicorn を ephemeral port（実ソケット）で別スレッド起動し、
    aiobotocore（path-style）から PUT→GET→HEAD→ListObjectsV2(flat+delimiter)→DELETE を往復＋NoSuchKey/AccessDenied を
    実クライアント上で検証。**実 client は XML/ヘッダに厳密だが齟齬ゼロ**（ETag・ContentLength・XML 名前空間・エラー
    Code・ステータスコードすべて botocore がそのまま受理）。S4 の **SeaweedFS 実機疎通**（パススルー含む）は別途残す。
- **S2 multipart PUT（optional・大容量）**: CreateMultipartUpload/UploadPart/Complete/Abort を
  `S3FileStore` の multipart writer に橋渡し。大容量 PUT が通る。
  - 受け入れ: 大きめオブジェクトの multipart PUT→GET 一致。`make check` 緑。
- **S3 パススルー（optional・backend=s3）**: `backends/s3.py` に `SupportsPresign` 実装＋
  `gateway/passthrough.py`（redirect/proxy 分岐・`--passthrough` スイッチ）。
  - 受け入れ: **backend=s3 のとき** GET/PUT で **307 presigned redirect が観測**できる（`passthrough=redirect`）／
    リダイレクト不可条件で **プロキシにフォールバック**することを観測（テストで両分岐）。`make check` 緑。
- **S4 実 backend 疎通**: （実 S3 **クライアント**往復は S1 後続で前倒し完了＝`test_gateway_s3client.py`。残るは実
  **backend** = SeaweedFS）`make e2e-up`（SeaweedFS）で gateway 越し GET/PUT/LIST を実機確認。
  backend=s3 パススルーは SeaweedFS の presigned で redirect/proxy を実観測。activeContext に結果記録。
  - 受け入れ: 実 SeaweedFS で疎通。`make check`（既存 + 新規）緑。

---

## 受け入れ条件（指示対応・総括）

- ゲートウェイ経由で**代表 backend に GET/PUT/LIST が通る**（S1）。
- **backend=s3 でパススルー（redirect）またはフォールバック（proxy）が観測できる**（S3）。
- `uv run pytest` / `make check` 緑（各ステップ）。
- **実 backend（SeaweedFS）で疎通**（S4）。

## 未決事項（ユーザー/supervisor 判断が要る）

> S1 着手時に supervisor 指示で推奨デフォルトを適用済みの項目は ✅ で注記。

1. **`passthrough` 既定値**: `proxy`（常に動く・安全）か `redirect`（直送を既定で体感）か `off` か。**未決（S3 で要判断）**。
2. ✅ **クライアント↔gateway の SigV4 署名検証**＝**しない**（gateway 認証へ委譲）で S1 確定。
3. ✅/⏳ **ListObjectsV2 のページング**＝S1 は max-keys 上限 1000 でクランプ・打ち切りのみ。
   **continuation token は繰延**（backend M012 pagination と合流させる方針・要判断）。
4. **presigned の既定 TTL**（暫定 900 秒）。**未決（S3 で要判断）**。
5. パススルー直送時に実 S3 のキー制約へ従う（`validate_safe_path` が効かない）旨を**仕様として許容**するか。**未決（S3）**。
6. ✅ extra ＝ **`[server]` 相乗り**で確定（`[gateway]` 新設せず＝YAGNI）。

## スコープ越境ガード（deep think の姿勢）

- コア IF（`KeyValueStore`/`FileStore`）に S3 都合（presign/ACL/タグ）を**足さない**。presign は s3 backend 限定の
  optional capability に閉じ、gateway が能力検出する。他 backend・抽象は不変。
- multipart・パススルーは optional 段階。本体（S1）が動けば段階導入でよい（指示の優先度 normal に合致）。
- bucket=context・config 共有で UI 版と設定資産を共有＝新概念を増やさない。
