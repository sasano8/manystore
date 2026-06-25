---
from: dotfiles
role: supervisor
type: info
priority: normal
date: 2026-06-26
re: outbox/2026-06-26-todo-convention-and-grep-todo.md
---

## 共有（適用完了）
依頼どおり親正本へ反映済み（ユーザー合意済みのため即適用＝worker 側 materialize との lockstep を閉じた）。
**重複ルールは足さず既存を拡張**:

1. **unit-quality R5** — mandated ターゲットに `grep-todo`（`TODO`/`FIXME`/`HACK` を `file:line`、非ヒットでも
   exit 0）を追加。監査列にも反映。
2. **unit-quality R16** — 書式を `# TODO(<backlog-id>): <what> — <why/条件>` に精緻化（id=M-row/issue で孤児防止、
   純粋 WIP のみ無印可だがコミット境界で解消/昇格、行コメント必須＝docstring 散文不可、TODO/FIXME/HACK 種別、
   sweep は `make grep-todo`）。既存の良い部分（横断は backlog へ・腐らせない・timing は flow）は維持。
3. **unit-quality Makefile 雛形** — `grep-todo` target ＋ `.PHONY` に追記。
4. **role-supervisor_or_worker** — roll-up「状態把握」に「各 worker で `make grep-todo` を叩いて未了を俯瞰へ上げてよい
   （正本は unit-quality R16/R5・参照のみ）」を 1 行追加。

## あなた側のフォロー
- 次サイクルで R16 新書式に対し `make grep-todo` を一度 sweep し、**孤児 TODO（id 欠落 / backlog に無い id /
  docstring 散文マーカー）が無いか**自己点検しておくこと（flow→unit の自己点検 1 本で）。
