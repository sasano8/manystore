# 名前空間の再編: kv（buffered）/ storage（streaming）

ユーザー要望（2026-06-23・対話）。統合エントリポイント（M023）の HTTP 面を、
**バッファリング性**を第1軸に再編する。

## 軸と分類（ユーザーの整理）

- **kv** = バッファする（値まるごと・application/json のような小さい辞書オブジェクト的）。単純 get/put。
- **storage** = 基本バッファしない（ストリーミング・ファイルオープン的）。ラージファイル。
- s3 は意味的には kv（key→bytes）だが、**ラージファイル＋multipart でファイルオープン寄り**ゆえ storage 側。
- **kv/json** = サーバ内で「json か」検証し、必ず json を返すもの（値に型/検証を持ち込む）。

## 4 ルート

| ルート | 族 | 方言/エンコード | 実体 | 状態 |
|--------|----|---------------|------|------|
| kv/raw | kv(buffered) | 生バイト・不透明 | 今の /manystore objects | 既存 |
| kv/json | kv(buffered) | JSON検証（PUT検証→不正400・GETは必ず application/json） | KVS + JSON codec facade | 新規 |
| storage/s3 | storage(stream) | S3（GET/PUT/HEAD/List+multipart） | 今の /s3 | 既存 |
| storage/manystore | storage(stream) | manystore 独自ストリーミング | FileStore over HTTP | 新規（未公開） |

すべて server facade 層に閉じる＝**コア IF 不変**（S3 ゲートウェイと同じ流儀）。

## フェーズ

1. **移設（今回着手）** — 既存 2 ルートを新名前空間へ再配置。`/manystore`→`/kv/raw`、`/s3`→`/storage/s3`。
   combined アプリに閉じる。後方互換エイリアスは張らずクリーンに移設（未リリース）。
2. kv/json facade 追加。
3. storage/manystore（FileStore のストリーミング HTTP 公開）。← 一番重い新規。

## 開始指示

ユーザー「移設から」。→ フェーズ1 から着手する。
