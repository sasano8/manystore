# Active Context

## 現在のフォーカス

**M018（HTTP backend, read-only）完了。** ユーザー要望の HTTP read-only ストアを統合（前回未コミットだった
試作を完成させた）。`make check` 緑（51 passed, 1 skipped）。次サイクルは配布前提の G1（M005〜M008）が安く効く。
このプロジェクトは supervisor（dotfiles）の worker として `agent` ブランチで作業し、interrupt 指示を取り込んで
進める運用。

## 直近の変更

- **M018 完了（HTTP backend, read-only）**：ユーザー要望「http ストレージを read-only でよいから欲しい」を実装。
  `backends/http_store.py`（GET で `get`/`open("rb")`、HEAD で `exists`。404→None/FileNotFoundError。書き込み・
  一覧は `io.UnsupportedOperation`）。httpx を遅延 import。`create_key_value_store("http", http_base_url=...,
  http_headers=...)` 配線、`__init__.__all__`・README・テスト（fake httpx client で 4 ケース）整備。
  - **モジュール名**: 当初 `http.py` で作られていたが stdlib `http` パッケージと紛れるため `http_store.py` に
    リネーム（**backend 識別子は `"http"` のまま**）。ユーザー指摘。
  - **M005 修正**: httpx は当初「未使用＝削除」だったが http backend で使うので**残す**に変更。`redis` のみ未使用。
- **プロセスの穴をエスカレーション**：ユーザー要望（http backend）が**着手前に Memory Bank へ保存されず**、前回
  セッションで未コミットの試作だけが残っていた（活動記録なし）。memory-bank スキルの「要望は着手前に
  activeContext/タスクへ記録する」運用が抜けた件として、**dotfiles 側に上り受信箱を新設**して投函
  （`~/projects/dotfiles/.work/skills/memory-bank/interrupt/20260621-0100-manystore-escalation-skill-gap.md`）。
  上り（worker→親）経路はこれまで受信箱が無く実質ノーオペだったと判明（下り dotfiles→manystore は機能）。
- **UI 要望をバックログ化**：ユーザー要望「ストレージの UI が欲しい」を progress.md の **M019（相談）**へ。
  未スコープ＋本体スコープ外のため、別パッケージ/別リポか着手前に要合意。

- **juice 概念を削除**：manystore は juice と無関係な独立ライブラリなので、コード（`__init__`/`array_storage`/
  `tests`/`pyproject`/README）と Memory Bank から juice・E006・「pristine（juice 都合）」の記述を一掃。設計
  原則は「**最小・汎用に保つ（YAGNI）**」として残す。juice adapter のバックログ（旧 M005）も削除。
- **M002 一部完了**：docker（nats / seaweedfs）で `tests/test_e2e_backends.py` を**パラメタライズ**追加
  （同一 CRUD を local / nats / s3-virtual / s3-path に注入。実行 test は1つ、注入インスタンスだけ違う）。
  **local / nats は実機で pass**。S3 は実機検証で **アドレッシングスタイル問題を発見**し、`addressing_style` を
  **明示パラメータ化（既定 virtual＝ドメイン、`"path"` は opt-in）**に変更（`s3_addressing_style`）。
- **M002 完了**: SeaweedFS の S3 認証は `weed shell s3.configure` で dev identity（`manystore`/`manystoresecret123`,
  Admin）を登録して解決。`make e2e-up`（compose up + identity 登録）で 1 コマンド化し、テスト既定鍵もこの dev
  identity に。`make check` で **s3-path 実機 pass**（47 passed, 1 skipped）。s3-virtual はローカルでは原理的 skip。
- **M004 完了**：ルート `README.md` を作成（特徴・install・local/S3/NATS の接続例・`ConnectPolicy` プリセット・
  `Safe*` ラッパ・その他公開 API・開発/CI/3.14 注記）。公開 API は `manystore/__init__.py` の `__all__` に準拠。
- **M003 完了（supervisor 指示で着手）**：dotfiles（supervisor）が manystore の interrupt に投函した指示
  （`20260620-1200-m003-ci.md`, priority high）を取り込み、GitHub Actions CI（`.github/workflows/ci.yml`：
  setup-uv → `make check`）を追加。指示は `interrupt/archive/` へ退避。
- **Python 3.14+ 前提を確定**：3.14 は注釈遅延評価が既定なので前方参照（自クラス戻り値注釈）はそのまま valid＝
  `from __future__ import annotations` 不要。`requires-python = ">=3.14"` ＋ ruff `target-version = "py314"` に
  し future import を全廃。ruff は py314 対応版が要るので `RUFF_VERSION` を 0.15.18 へ。`make check` 緑（44 passed）。
- **M001 完了**：旧名残骸を監査（`git grep shoudou`）。実コードの残骸は NATS 既定バケット名のみで、
  `manystore/backends/__init__.py` の `nats_bucket="shoudou_files"`→`"manystore_files"` に変更（既定値のみ・
  テスト非依存）。`uv run pytest` で **44 passed**。
- 本セッションで `Makefile`（`uvx ruff@<固定版>` の format / `uv run pytest` の test）を追加（M003 の一部）。
- `shoudou_storage` を独立ライブラリ `manystore` として抽出し、import 名・プロジェクト名を `manystore` に
  統一。関連 commit: `f80ba87` / `1983fc7` / `2d28010`。
- Memory Bank を導入。当初は AGENT_LOOP.md / PROJECT.md の 2 ファイル構成だったが、
  **Cline の Memory Bank（6 コアファイル）に準拠**するよう作り直し、作業フォルダ
  `.work/skills/memory-bank/` 配下へ集約した。

## 次のステップ

- バックログ（progress.md）から優先タスクを 1 つ選定し、本ファイルの「現在のフォーカス」に展開。

## 進行中の決定・考慮事項

- **Memory Bank は Cline 準拠の 6 ファイル**（projectbrief / productContext / activeContext /
  systemPatterns / techContext / progress）。手順・運用は共通スキル `memory-bank`（`~/.claude/skills/`）に集約。
- 作業フォルダ規約は `.work/skills/<スキル名>/`。`.work/` は gitignore しない（状態の正本＝commit する）。
- **コミットをフローに組み込む**：Act Mode の終端で「切りのいいところ」（まとまり一段落＋検証緑＋
  Memory Bank 更新済み）になったら、コード＋Memory Bank を 1 コミットにまとめる。`main` 直は避け branch を切る。
  push は明示時のみ。
- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない。利用側固有の結線は利用側の adapter に閉じる。

## 重要なパターン・好み / 学び

- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる必要があった（過去バグ）。
