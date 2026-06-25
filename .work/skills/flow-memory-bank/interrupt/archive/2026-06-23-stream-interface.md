# stream インターフェース（第3の族）

ユーザー要望（2026-06-23・対話）。バックログ登録の指示（「タスクに積んで」）。

## 概要

storage / kv の他に **stream インターフェース**を追加する。

- 単一ファイル（単一ターゲット）を指定。
- それと**接続を繋いで入出力する**（接続を張り続けるチャネル）。
- jsonl のように**追記**していける。
- NATS のような**トピックをファイルと見なす**こともできる。
- **基本はバイトを流すところから始める**（MVP=byte stream）。

## 設計の位置づけ（着手前 deep think 用メモ）

3 族を buffer 軸で整理:

| 族 | 性質 | 中核プリミティブ | コア IF |
|----|------|----------------|---------|
| kv | バッファ・有限・ランダムアクセス | get/put | KeyValueStore（既存） |
| storage | ストリーム・**有限ファイル** | open_reader/open_writer | FileStore（既存） |
| **stream** | ストリーム・**無境界チャネル** | **append / follow(tail/subscribe)** | **StreamStore（新設・要設計）** |

- `storage`（FileStore）では表現できない**新プリミティブ＝tail/subscribe と継続 append**が核。
  → 既存 IF の拡張では足りず**新しいコア IF**になる（kv/json は facade で済んだが、これは違う）。
- projectbrief「最小・汎用に保つ / YAGNI」と緊張 → **doc-first で設計合意してから着手**。今回は登録のみ。
- MVP = バイトを流すところから。jsonl のレコード境界 / NATS トピックは、その上の
  **エンコード/backend 特化**として後段（kv/raw→kv/json と同じ重ね方）。
- HTTP 公開時は `stream/*` 族（WS / chunked / SSE）になるが更に先。

## 設計の未決（着手時に詰める）

- IF 名・形（`StreamStore.open(target) -> Stream`？ read()/write(append)/subscribe()？ 方向＝読み/書き/双方向）。
- 有限/無境界・replay 可否（jsonl はファイル先頭から replay 可・NATS は live のみ or JetStream 履歴）。
- バックエンド: local（追記ファイル＋tail）／NATS（subject の pub/sub）から。S3 は append 非対応＝対象外か。
- 接続ライフサイクル（既存 connect/ConnectPolicy との整合）。
- 既存 FileStore との境界（FileStore を無境界へ拡張するのでなく別 IF にする理由を明文化）。
