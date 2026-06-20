# Active Context

## 現在のフォーカス

**M001 / M003 / M004 完了。** 残りは **M002（実 backend 疎通）のみ**（docker 前提で環境依存＝着手前にユーザー確認）。
このプロジェクトは supervisor（dotfiles）の worker として `agent` ブランチで作業し、interrupt 指示を取り込んで
進める運用に入った。

## 直近の変更

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
  テスト非依存）。`pyproject.toml` の由来コメント（juice の旧 dependency-group 名）は provenance として意図的に保持。
  `uv run pytest` で **44 passed**。
- 本セッションで `Makefile`（`uvx ruff@<固定版>` の format / `uv run pytest` の test）を追加（M003 の一部）。
- juice から `shoudou_storage` を独立ライブラリ `manystore`（別 repo）として抽出。import 名・
  プロジェクト名を `manystore` に統一。関連 commit: `f80ba87` / `1983fc7` / `2d28010`。
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
- **manystore は pristine**：juice 都合の IF 拡張はしない。統合は juice 側 adapter に閉じる。

## 重要なパターン・好み / 学び

- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる必要があった（過去バグ）。
