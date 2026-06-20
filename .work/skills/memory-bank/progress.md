# Progress

## 動くもの（What works）

- 2 ストア抽象（`KeyValueStore` / `FileStore`）と backend 実装（local / s3 / nats）。
- async / sync / bridge（`AsyncToSyncKeyValueStore`）。
- 接続ライフサイクル（`connect_key_value_store` / `connecting` / `ConnectPolicy`）。
- 安全パス（`validate_safe_path` / `SafeKeyValueStore`〔download・キャッシュ含む〕 / `SafeFileStore`）。
- 合成ストア（`ArrayKeyValueStore` / `DownloadCache`）。
- **テスト**: `uv run pytest` で **44 passed**（S3 / NATS は in-memory fake で検証）。
- **CI**: GitHub Actions（`.github/workflows/ci.yml`）で push/PR 時に `make check`（ruff format-check + check + pytest）。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

| ID | タスク | 状態 | 備考 |
|----|--------|------|------|
| M001 | 旧 `shoudou_storage` 残骸の掃除（docstring/コメント） | 完了 | NATS 既定バケット `shoudou_files`→`manystore_files`。残るは pyproject の由来コメントのみ（意図的に保持） |
| M002 | 実 backend（minio / 実 NATS）での E2E 疎通検証 | 未着手 | 現状 fake 担保。`docker-compose.yml` で起動して実疎通 |
| M003 | CI（GitHub Actions）＋ lint/format 統一 | 完了 | `.github/workflows/ci.yml`（setup-uv→`make check`）。supervisor 指示で着手。あわせて **Python 3.14+ 前提**を確定（後述） |
| M004 | README / ドキュメント整備 | 完了 | ルート `README.md` 作成（特徴・install・quickstart・backend別接続・ConnectPolicy・Safe・開発/CI/3.14）|
| M005 | juice からの利用（adapter）に向けた IF 確認 | 保留 | juice 側 src に adapter（manystore は pristine 維持）。追加要件が出たらここに |

## 現状ステータス

抽出・独立ライブラリ化は完了し単体で緑。**M001 / M003 / M004 完了**。残バックログは **M002（実 backend 疎通）**
のみ。M003 は supervisor（dotfiles）からの interrupt 指示で着手し CI（`make check`）を追加、あわせて Python 3.14+
前提を確定（下記）。M004 でルート README 作成。

## 既知の問題

- S3 / NATS backend は in-memory fake でのみ検証済み。**実機（minio / 実 NATS）疎通は未検証**（M002・残）。
- ~~ルート README が無い~~（M004 で解消）。
- ~~CI 未設定~~（M003 で解消）。

## 意思決定の変遷

- ストレージ抽象を juice から切り出す方針（juice 課題 E006）。juice は将来「利用する側」になり、結線は
  juice 側 adapter に閉じる（manystore は pristine）。
- **Python 3.14+ を前提に確定（M003）**: 3.14 は注釈遅延評価（PEP 649）が既定なので、自クラス等を戻り値
  注釈に使う前方参照はそのまま valid＝`from __future__ import annotations` は不要。当初 `requires-python>=3.10`
  だったため ruff が forward-ref を F821 と判定し future import を入れたが、方針は「3.14+ 前提」なので撤回。
  `requires-python = ">=3.14"` ＋ ruff `target-version = "py314"` にし、future import を全廃。ただし ruff は
  **py314 対応版が必須**（0.9.1 は py314 未対応）→ `RUFF_VERSION` を **0.15.18** に更新。
- Memory Bank: 独自 2 ファイル構成 → **Cline 準拠 6 ファイル**へ移行。作業フォルダは `.cache/` 案 →
  `.work/skills/memory-bank/` に確定（`.cache/` は「捨てる」含意のため不可。`.work/` は commit する状態）。
