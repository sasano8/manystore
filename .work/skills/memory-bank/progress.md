# Progress

## 動くもの（What works）

- 2 ストア抽象（`KeyValueStore` / `FileStore`）と backend 実装（local / s3 / nats / **http**）。
  http は **read-only**（`get`/`exists`/`open("rb")` のみ。書き込み・一覧は `io.UnsupportedOperation`）。
  モジュールは stdlib `http` と紛れないよう `backends/http_store.py`（backend 識別子は `"http"`）。
- async / sync / bridge（`AsyncToSyncKeyValueStore`）。
- 接続ライフサイクル（`connect_key_value_store` / `connecting` / `ConnectPolicy`）。
- 安全パス（`validate_safe_path` / `SafeKeyValueStore`〔download・キャッシュ含む〕 / `SafeFileStore`）。
- 合成ストア（`ArrayKeyValueStore` / `DownloadCache`）。
- **テスト**: `uv run pytest` で **51 passed, 1 skipped**（S3 / NATS / HTTP は in-memory fake で検証）。
- **CI**: GitHub Actions（`.github/workflows/ci.yml`）で push/PR 時に `make check`（ruff format-check + check + pytest）。
- **実 backend 疎通**: NATS / S3（path-style）を実機 E2E で検証済み（`tests/test_e2e_backends.py`、`make e2e-up`）。
  パラメタライズで local / nats / s3-virtual / s3-path に同一 CRUD を注入。`make check` で 47 passed, 1 skipped
  （s3-virtual はローカル S3互換では原理的に skip）。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

| ID | タスク | 状態 | 備考 |
|----|--------|------|------|
| M001 | 旧 `shoudou_storage` 残骸の掃除（docstring/コメント） | 完了 | NATS 既定バケット `shoudou_files`→`manystore_files`（既定値のみ・テスト非依存）|
| M002 | 実 backend（S3 / 実 NATS）での E2E 疎通検証 | 完了 | NATS / S3(path) を実機 E2E で検証。`make e2e-up` が SeaweedFS に dev identity（`weed shell s3.configure`）を登録し、`make check` で s3-path も通る（47 passed, 1 skipped）。s3-virtual はローカルでは原理的 skip |
| M003 | CI（GitHub Actions）＋ lint/format 統一 | 完了 | `.github/workflows/ci.yml`（setup-uv→`make check`）。supervisor 指示で着手。あわせて **Python 3.14+ 前提**を確定（後述） |
| M004 | README / ドキュメント整備 | 完了 | ルート `README.md` 作成（特徴・install・quickstart・backend別接続・ConnectPolicy・Safe・開発/CI/3.14）|
| M018 | HTTP backend（read-only）追加 | 完了 | **ユーザー要望**。GET/HEAD で取得する read-only ストア（`get`/`exists`/`open("rb")`）。`backends/http_store.py`（stdlib `http` 回避でリネーム）、`create_key_value_store("http", http_base_url=..., http_headers=...)`、`__all__`/README/テスト整備。httpx を遅延 import。51 passed |

### 評価で洗い出した改善バックログ（2026-06-21、目標 G1〜G4）

優先度: 高=安く無害高効果/配布の前提、中=実運用品質、相談=トレードオフ判断。

| ID | タスク | 優先 | 目標 | 備考 |
|----|--------|------|------|------|
| M005 | 未使用依存 `redis` を削除 | 高 | G1 | `redis` はどこも import していない（juice 抽出残骸）。~~httpx~~ は **http backend で使用するので残す**（当初は未使用だったが M018 で http backend を追加）。S3=aiobotocore / NATS=nats-py / HTTP=httpx / local=stdlib |
| M006 | LICENSE 追加 | 高 | G1 | OSS 配布の必須要件。現状ゼロ |
| M007 | `py.typed` 追加（PEP 561） | 高 | G1 | 型ヒントを書いているのに配布で効かない |
| M008 | PyPI メタデータ整備 | 高 | G1 | authors/license/readme/classifiers/urls/keywords。現状 name/version/description のみ |
| M009 | 統一例外階層 `ManystoreError`（+ NotFound 等） | 中 | G2 | stdlib 例外混在。backend が広い except で握りつぶす箇所（nats get/exists が任意 Exception を None 化）を整理 |
| M010 | local backend の非ブロッキング化 | 中 | G2 | `read_bytes`/`write` を async 内で同期実行＝event loop を塞ぐ。`asyncio.to_thread` でオフロード |
| M011 | 既定で安全（キー検証）/方針明確化 | 中 | G2 | 生 backend はキー検証なし＝`../escape` で脱出可。安全が `Safe*` opt-in の foot-gun |
| M012 | `list(prefix=...)` / pagination | 中 | G3 | 現状 limit のみ。prefix 絞り込み・継続トークンが無く大量キーで非効率 |
| M013 | メタデータ / content-type | 中 | G3 | S3・NATS はネイティブ対応だが共通 IF に無い |
| M014 | 操作レベル retry/timeout | 低 | G3 | 現状 connect のみ。put/get の一時失敗に未対応 |
| M015 | logging（操作・リトライの可視化） | 低 | G3 | 観測性なし |
| M016 | テスト拡充（エラーパス/並行/大容量） | 中 | G2 | fake は happy path 中心 |
| M017 | Python サポート範囲（3.10+ へ広げるか） | 相談 | G4 | `>=3.14` は採用障壁。広げるなら future import 復活＋ruff 設定。3.14純度 vs 採用のトレードオフ |

**ゴール（段階）**: G1=配布できる（M005〜M008）→ G2=安心して使える（M009〜M011・M016）→
G3=機能十分（M012〜M015）→ G4=広く使える（M017 判断）。

## 現状ステータス

独立ライブラリ化は完了し M001〜M004 完了（実 backend 疎通 / CI / README / 3.14 化）。**評価により次フェーズの
改善バックログ M005〜M017 を洗い出し**（上記）。直近は配布前提の G1（未使用依存削除・LICENSE・py.typed・メタ）
が安く効く。M002 は NATS / S3(path) を実機 E2E で検証済み。

## 既知の問題

- ~~S3 の実機検証は保留~~（M002 で解消。`make e2e-up` が SeaweedFS に dev identity を登録し s3-path 実証）。
- `s3-virtual`（ドメインスタイル）はローカル S3互換では `bucket.<host>` を名前解決できず常に skip。これは
  **virtual-host の仕様上の制約**（実 AWS 等の DNS 環境向け）であり未解決バグではない。
- ~~ルート README が無い~~（M004 で解消）。~~CI 未設定~~（M003 で解消）。

## 意思決定の変遷

- ストレージ抽象は独立ライブラリとして自己完結させる。利用側固有の結線は利用側の adapter に閉じ、
  manystore 本体は最小・汎用に保つ（IF を利用側都合で拡張しない）。
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
