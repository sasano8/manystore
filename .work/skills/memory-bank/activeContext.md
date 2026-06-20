# Active Context

## 現在のフォーカス

**未着手。** 次サイクルでバックログ（progress.md の「残作業」M001〜）から 1 つ選び、ここに
ゴール／タスク（チェックリスト）／完了条件を書いてから着手する。候補の優先度
（M002 実 backend 疎通 / M003 CI・lint 統一 / M004 README）は着手前にユーザーへ確認するとよい。

## 直近の変更

- juice から `shoudou_storage` を独立ライブラリ `manystore`（別 repo）として抽出。import 名・
  プロジェクト名を `manystore` に統一。`uv run pytest` で 44 passed。
  関連 commit: `f80ba87` / `1983fc7` / `2d28010`。
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
