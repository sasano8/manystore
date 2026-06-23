# M025 名前空間再編: kv（buffered）/ storage（streaming）

統合エントリポイント（M023）の HTTP 面を **バッファリング性**を第1軸に再編する計画。
対話で確定した設計（2026-06-23）。

## 設計の核（なぜこの軸か）

manystore のコア抽象は 2 つ:

- `KeyValueStore`（put/get/list…）= 値まるごと get/put＝**バッファ**。辞書オブジェクト的。
- `FileStore`（open_reader/open_writer→FileObject）= **ストリーミング**。ファイルオープン的。

現状の `/manystore` ネイティブ REST は **KeyValueStore に 1:1 の薄い HTTP アダプタ**で、
object の GET/PUT は **生バイトを素通し**（`application/octet-stream`、値の中身は解釈しない）、
一覧/contexts/イベントだけ dataclass→JSON。＝実質すでに「kv の生バイト版」。

ユーザーの再編軸は「どの IF か」ではなく **buffer vs stream**:

- **kv** = バッファする（小さい値・辞書的・全体 get/put）。
- **storage** = バッファしない（ストリーミング・ラージファイル・ファイルオープン）。
- s3 は意味的に kv（key→bytes）だが、ラージ＋multipart で**ファイルオープン寄り**ゆえ storage 側へ。

## 4 ルート（第1階層=buffer性、第2階層=方言/エンコード）

| ルート | 族 | 方言/エンコード | 実体 | 状態 |
|--------|----|---------------|------|------|
| `kv/raw` | kv(buffered) | 生バイト・不透明 | 今の `/manystore` objects（KVS 1:1） | 既存 |
| `kv/json` | kv(buffered) | JSON 検証（PUT で json 妥当性検証→不正 400 / GET は必ず `application/json`） | KVS + JSON codec facade | 新規 |
| `storage/s3` | storage(stream) | S3（GET/PUT/HEAD/List + multipart） | 今の `/s3` ゲートウェイ | 既存 |
| `storage/manystore` | storage(stream) | manystore 独自ストリーミング | FileStore over HTTP | 新規（未公開） |

すべて **server facade 層**に閉じる＝コア IF（KeyValueStore/FileStore/StorageService）不変。
S3 ゲートウェイ・既存 native REST と同じ流儀（YAGNI と両立＝core を触らない）。

## フェーズ

### フェーズ1: 移設（既存 2 ルートの再配置）← 着手中

combined アプリ（`manystore/combined.py`）の `include_router` prefix を付け替えるだけ:

- `/manystore` → `/kv/raw`
- `/s3` → `/storage/s3`

結果のパス:
- `/kv/raw/contexts`、`/kv/raw/contexts/{ctx}/keys`、`/kv/raw/contexts/{ctx}/objects/{key}`、WS `/kv/raw/contexts/{ctx}/events`
- `/storage/s3/{bucket}/{key}`（S3 クライアントは `endpoint_url=<host>/storage/s3`）

範囲:
- 変更は **combined アプリに閉じる**。standalone（`create_app`/`create_gateway`・`python -m manystore.server`/`.gateway`）は不変＝旧パスが要る人はそちら。
- **後方互換エイリアスは張らない**（M023 は未リリース＝外部利用者ゼロ。クリーンに移設）。
- 触るファイル: `combined.py`（prefix・docstring）／`__main__.py`（docstring・help）／`tests/ui/test_combined.py`（URL 更新）。
- native の内部パス（`/contexts/.../objects/...`）は**そのまま**＝prefix 付け替えのみ（最小・可逆）。
  s3 がフラット（`/{bucket}/{key}`）なのに対し kv が深いのは、native が contexts/keys/events を持つ
  リッチなプロトコルだから（無理に平坦化しない）。
- コア IF 不変・新依存ゼロ。`make check` 緑維持（91/1）。

### フェーズ2: kv/json facade

`kv/raw` の上に JSON codec を 1 枚。PUT body を `json.loads` で検証（不正→400）、
GET は `application/json` を保証。保存方式（受信 bytes 素通し vs 正規化 re-serialize）は着手時に決める。

### フェーズ3: storage/manystore（FileStore streaming over HTTP）

未公開の `FileStore`（open_reader/open_writer）をストリーミング HTTP で公開する新面。一番重い新規。
チャンク転送・range・大容量を見据える。設計は着手時に deep think。

## 残課題 / 未決

- フェーズ2 の json 保存方式（素通し or 正規化）。
- フェーズ3 のストリーミングプロトコル詳細（range / chunked / multipart との関係）。
- README / examples の起動例パス更新（移設に伴い `/manystore`→`/kv/raw` 等。フェーズ1 で追従するか確認）。
