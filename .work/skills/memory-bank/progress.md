# Progress

## 動くもの（What works）

- 2 ストア抽象（`KeyValueStore` / `FileStore`）と backend 実装（local / s3 / nats）。
- async / sync / bridge（`AsyncToSyncKeyValueStore`）。
- 接続ライフサイクル（`connect_key_value_store` / `connecting` / `ConnectPolicy`）。
- 安全パス（`validate_safe_path` / `SafeKeyValueStore`〔download・キャッシュ含む〕 / `SafeFileStore`）。
- 合成ストア（`ArrayKeyValueStore` / `DownloadCache`）。
- **テスト**: `uv run pytest` で **44 passed**（S3 / NATS は in-memory fake で検証）。
- **CI**: GitHub Actions（`.github/workflows/ci.yml`）で push/PR 時に `make check`（ruff format-check + check + pytest）。
- **実 backend 疎通**: NATS / S3（path-style）を実機 E2E で検証済み（`tests/test_e2e_backends.py`、`make e2e-up`）。
  パラメタライズで local / nats / s3-virtual / s3-path に同一 CRUD を注入。`make check` で 47 passed, 1 skipped
  （s3-virtual はローカル S3互換では原理的に skip）。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

| ID | タスク | 状態 | 備考 |
|----|--------|------|------|
| M001 | 旧 `shoudou_storage` 残骸の掃除（docstring/コメント） | 完了 | NATS 既定バケット `shoudou_files`→`manystore_files`。残るは pyproject の由来コメントのみ（意図的に保持） |
| M002 | 実 backend（S3 / 実 NATS）での E2E 疎通検証 | 完了 | NATS / S3(path) を実機 E2E で検証。`make e2e-up` が SeaweedFS に dev identity（`weed shell s3.configure`）を登録し、`make check` で s3-path も通る（47 passed, 1 skipped）。s3-virtual はローカルでは原理的 skip |
| M003 | CI（GitHub Actions）＋ lint/format 統一 | 完了 | `.github/workflows/ci.yml`（setup-uv→`make check`）。supervisor 指示で着手。あわせて **Python 3.14+ 前提**を確定（後述） |
| M004 | README / ドキュメント整備 | 完了 | ルート `README.md` 作成（特徴・install・quickstart・backend別接続・ConnectPolicy・Safe・開発/CI/3.14）|
| M005 | juice からの利用（adapter）に向けた IF 確認 | 保留 | juice 側 src に adapter（manystore は pristine 維持）。追加要件が出たらここに |

## 現状ステータス

抽出・独立ライブラリ化は完了し単体で緑。**M001〜M004 すべて完了**。M002 は NATS / S3(path) を実機 E2E で検証
（`make e2e-up`＋パラメタライズテスト）。M003 は supervisor（dotfiles）の interrupt 指示で着手し CI を追加、
あわせて Python 3.14+ 前提を確定。M004 でルート README 作成。**バックログは（保留の M005 を除き）一掃**。

## 既知の問題

- ~~S3 の実機検証は保留~~（M002 で解消。`make e2e-up` が SeaweedFS に dev identity を登録し s3-path 実証）。
- `s3-virtual`（ドメインスタイル）はローカル S3互換では `bucket.<host>` を名前解決できず常に skip。これは
  **virtual-host の仕様上の制約**（実 AWS 等の DNS 環境向け）であり未解決バグではない。
- ~~ルート README が無い~~（M004 で解消）。~~CI 未設定~~（M003 で解消）。

## 意思決定の変遷

- ストレージ抽象を juice から切り出す方針（juice 課題 E006）。juice は将来「利用する側」になり、結線は
  juice 側 adapter に閉じる（manystore は pristine）。
- **S3 アドレッシングスタイルを明示パラメータ化（M002 で発見）**: 既定の virtual-host だと S3 互換サーバ
  （minio/SeaweedFS）で `bucket.<host>` を名前解決できず接続不可。fake テストでは気づけず実機 E2E で露見。
  方針は「**既定 virtual（ドメイン）、利用側が `"path"` を opt-in**」。`S3*Store(addressing_style="virtual")`、
  `create_key_value_store(s3_addressing_style=...)`、`connect_key_value_store("s3", s3_addressing_style="path")`。
  実 AWS は既定 virtual のまま。
- **E2E テストはパラメタライズ**（`tests/test_e2e_backends.py`）: 同一 CRUD を local / nats / s3-virtual /
  s3-path に注入して回す（実行する test は1つ、注入インスタンスだけ違う）。各ケースは未到達/認証未整備なら skip。
- **Python 3.14+ を前提に確定（M003）**: 3.14 は注釈遅延評価（PEP 649）が既定なので、自クラス等を戻り値
  注釈に使う前方参照はそのまま valid＝`from __future__ import annotations` は不要。当初 `requires-python>=3.10`
  だったため ruff が forward-ref を F821 と判定し future import を入れたが、方針は「3.14+ 前提」なので撤回。
  `requires-python = ">=3.14"` ＋ ruff `target-version = "py314"` にし、future import を全廃。ただし ruff は
  **py314 対応版が必須**（0.9.1 は py314 未対応）→ `RUFF_VERSION` を **0.15.18** に更新。
- Memory Bank: 独自 2 ファイル構成 → **Cline 準拠 6 ファイル**へ移行。作業フォルダは `.cache/` 案 →
  `.work/skills/memory-bank/` に確定（`.cache/` は「捨てる」含意のため不可。`.work/` は commit する状態）。
