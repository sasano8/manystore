# Progress

## 動くもの（What works）

- 2 ストア抽象（`KeyValueStore` / `FileStore`）と backend 実装（local / s3 / nats）。
- async / sync / bridge（`AsyncToSyncKeyValueStore`）。
- 接続ライフサイクル（`connect_key_value_store` / `connecting` / `ConnectPolicy`）。
- 安全パス（`validate_safe_path` / `SafeKeyValueStore`〔download・キャッシュ含む〕 / `SafeFileStore`）。
- 合成ストア（`ArrayKeyValueStore` / `DownloadCache`）。
- **テスト**: `uv run pytest` で **44 passed**（S3 / NATS は in-memory fake で検証）。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

| ID | タスク | 状態 | 備考 |
|----|--------|------|------|
| M001 | 旧 `shoudou_storage` 残骸の掃除（docstring/コメント） | 未着手 | import 名は統一済。文字列に旧名が残っていないか |
| M002 | 実 backend（minio / 実 NATS）での E2E 疎通検証 | 未着手 | 現状 fake 担保。`docker-compose.yml` で起動して実疎通 |
| M003 | CI（GitHub Actions）＋ lint/format 統一 | 未着手 | `ruff` + `pytest`。`make check` 相当のワンコマンド化 |
| M004 | README / ドキュメント整備 | 未着手 | ルート README が無い。公開 API・使い方・接続情報を記載 |
| M005 | juice からの利用（adapter）に向けた IF 確認 | 保留 | juice 側 src に adapter（manystore は pristine 維持）。追加要件が出たらここに |

## 現状ステータス

抽出・独立ライブラリ化は完了し単体で緑。次は実 backend 疎通 / CI / README のいずれかから着手予定（未選定）。

## 既知の問題

- S3 / NATS backend は in-memory fake でのみ検証済み。**実機（minio / 実 NATS）疎通は未検証**（M002）。
- ルート README が無い（M004）。CI 未設定（M003）。

## 意思決定の変遷

- ストレージ抽象を juice から切り出す方針（juice 課題 E006）。juice は将来「利用する側」になり、結線は
  juice 側 adapter に閉じる（manystore は pristine）。
- Memory Bank: 独自 2 ファイル構成 → **Cline 準拠 6 ファイル**へ移行。作業フォルダは `.cache/` 案 →
  `.work/skills/memory-bank/` に確定（`.cache/` は「捨てる」含意のため不可。`.work/` は commit する状態）。
