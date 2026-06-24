# M025 名前空間再編: kv（buffered）/ storage（streaming）＋ bucket/path addressing

統合エントリポイント（M023）の HTTP 面を **(A) バッファリング性**で名前空間に分け、
**(B) bucket/path で ArrayStorage をそのまま公開**する計画。対話で確定（2026-06-23 初版 → 2026-06-24 改訂）。

> **2026-06-24 改訂の要点**: フェーズ1（移設）は実装済。その後 M028 で context=ArrayStorage 第一階層に
> したのを受け、addressing を `contexts/{ctx}/objects/{key}` → **`{bucket}/{path}`** に簡素化し、
> **prefix を native API から撤去して backend/ラッパーの capability に移設**する方針を追記。

## 設計の核

### (A) 名前空間 = 転送セマンティクス（buffer vs stream）

- **kv** = バッファする（小さい値・辞書的・全体 get/put）。`KeyValueStore` 1:1。
- **storage** = バッファしない（ストリーミング・ラージ・ファイルオープン）。`FileStore` / S3。
- s3 は意味的に kv（key→bytes）だが、ラージ＋multipart で**ファイルオープン寄り**ゆえ storage 側。

### (B) パス = 場所（ArrayStorage のキーそのまま）

M028 で `StorageService` は `ArrayKeyValueStore` バック＝context は第一階層 mount。よって HTTP も
**`NS/{bucket}/{path}`** で十分（`{bucket}/{path}` がそのまま ArrayStorage キー `<mount>/<subkey>`）。
`contexts`/`objects`/`keys` の飾りは廃止。表層語は **`bucket`** に統一（S3 と揃える。内部 `StorageService`
の `context` 命名はそのままでも可）。

- **`{path}` は不透明**（`{path:path}` で丸ごと捕捉）。サーバは階層解釈しない。
- コレクション判別：**空パス = 一覧 / 非空パス = オブジェクト**。prefix（仮想フォルダ）を廃止したので
  末尾スラッシュ規則は不要（曖昧さが消える）。
- 予約語（objects/keys/events）が消えるので、その名の bucket/キーがあっても衝突しない。

## エンドポイント（改訂版）

native 系（`NS` = `/kv/raw` / `/kv/json` / `/storage/manystore`）共通形:

| メソッド | パス | 意味 |
|---|---|---|
| `GET` | `NS/` | bucket(=mount) 一覧 |
| `GET` | `NS/{bucket}/` | bucket 内の全キー（**フラット**・`?limit=`） |
| `WS` | `NS/{bucket}/` | 変更イベント購読（同パスを WS upgrade で判別） |
| `HEAD` | `NS/{bucket}/{path}` | 存在 |
| `GET` | `NS/{bucket}/{path}` | 取得（storage は `Range:` で 206/streaming） |
| `PUT` | `NS/{bucket}/{path}` | 書込（storage は chunked/large） |
| `DELETE` | `NS/{bucket}/{path}` | 削除 |

名前空間ごとの違い（HOW だけ）:
- `/kv/raw` … 生バイト・whole get/put（既存 native REST 相当）。
- `/kv/json` … PUT で JSON 検証（不正 400）／GET は `application/json`（フェーズ2）。
- `/storage/manystore` … FileStore over HTTP・`Range`/chunked（フェーズ3・一番重い）。

S3 だけ別形（互換のため・ワイヤ形式は S3 クライアントが決める）:
- `/storage/s3/{bucket}/{key}`（GET/PUT/HEAD/DELETE）＋ ListObjectsV2（`?list-type=2&prefix=&delimiter=`）＋ Multipart。
- **S3 は prefix を保持**（ListObjectsV2 は S3 仕様）。ただし実装は下記 capability 経由にする。

## prefix を capability に移設（native から撤去）

prefix は NATS 由来ではなく `service.list_entries` の**汎用 startswith フィルタ**。これを:

- **native HTTP API から撤去**（bucket 単位フラット list-all のみ）。`service.list_entries` の prefix 引数も撤去
  （または list-all に縮小）。
- **optional capability に移設**（core IF は最小のまま）:
  ```python
  class SupportsPrefixListing(Protocol):
      def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]: ...
  ```
  汎用フォールバックヘルパ `iter_prefix(store, prefix)`＝store がネイティブ実装を持てばそれ、無ければ
  `iter_all()` + `startswith` で総なめ。
- backend 別: **S3 = ネイティブ**（`list_objects_v2(Prefix=…)`＝サーバ側で絞る）／NATS・HTTP・local・dict =
  未実装（汎用フォールバック＝現状の総なめと同等）。
- **伝播**: native 効率を活かすため `SafeKeyValueStore` / `ArrayKeyValueStore` が `iter_prefix` を委譲する
  （Safe は prefix を validate して内側へ、Array は第一セグメントで mount にルーティング）。**M027b と同じ
  capability 伝播パターン**。委譲が無いと S3 native が隠れて総なめに落ちる。
- 使用元差し替え: **S3 ゲートウェイ ListObjectsV2** と **multipart 内部の part 列挙** を `iter_prefix` ヘルパ経由に。

## フェーズ / タスク

- **フェーズ1: 移設**（`/manystore`→`/kv/raw`・`/s3`→`/storage/s3`）＝**実装済（2026-06-23・91/1）**。
- **M025改: addressing 再設計**（`contexts/{ctx}/objects/{key}` → `{bucket}/{path}`・prefix 撤去・WS 同パス化）。
  破壊的変更（未リリース＝互換エイリアス無し）。触る: `server/routes.py`・`combined.py`・`client/remote.py`・
  `server/static/app.js`（フラット list-all + クライアント側畳み）・`tests/ui/*`・README/examples。
- **M030: prefix capability**（`SupportsPrefixListing` + 汎用ヘルパ + S3 native + Safe/Array 伝播 +
  gateway/multipart 差し替え）。
- **フェーズ2: kv/json facade**（PUT で json 検証→400 / GET は application/json。保存方式=素通し vs 正規化 未決）。
- **フェーズ3: storage/manystore**（FileStore streaming over HTTP・range/chunked・一番重い新規）。

## 未決
- M025改 と M030 の着手順（addressing 先 / capability 先 / 並行）。capability は gateway が今 `service.list_entries`
  経由なので、addressing 撤去と整合させて一度に切るのが安全か。
- `GET NS/`（bucket 一覧）に featured/default を載せるか（簡素化に倣い storage には出さない案）。
- フェーズ3 のストリーミングプロトコル詳細（range / chunked / multipart との関係）。
- UI のフラット list-all 化に伴う大規模 bucket の遅延ロード喪失（後回し可）。
