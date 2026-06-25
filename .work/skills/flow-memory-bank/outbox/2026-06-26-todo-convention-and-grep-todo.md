---
from: manystore
role: worker
type: escalation
date: 2026-06-26
source: ~/.claude/skills/unit-quality/SKILL.md（R5 / R16）, ~/.claude/skills/role-supervisor_or_worker/SKILL.md
---

## 上げたいこと
ユーザー要望：「`make grep-todo` でソース中の TODO を拾う」＋「TODO の規約を定義する」スキルを作りたい。
配置はユーザーと合意済み（**unit-quality に定義／supervisor・flow は参照**、書式は **backlog-id 必須**）。
worker からは親正本（dotfiles の skill）を編集できないため、以下を supervisor 側で適用してほしい。
**重複ルールを足さない**こと（既に R16「タスクの残し方（inline TODO）」がある＝そこを拡張する。drift 防止）。

### 1) unit-quality R5（Makefile 規約）に `grep-todo` を追加
mandated ターゲットを `format/format-check/lint/test/check` ＋ **`grep-todo`** に。
- 説明: `grep-todo` はソース中の作業マーカー（`TODO`/`FIXME`/`HACK`）を `file:line` で拾う＝R16 を materialize。
- 監査列にも「`grep-todo` 有無（R16）」を追加。

### 2) unit-quality R16（inline TODO 規約）を **backlog-id 必須**へ精緻化
現状 R16 は「`TODO:`/`FIXME:`・必要なら `TODO(誰):`」。これを次へ更新（既存の良い部分＝
横断は backlog へ・腐らせない・timing は flow、は維持）:
- **書式**: `# TODO(<backlog-id>): <what> — <why/条件>`。`<backlog-id>` は progress.md の M-row（または issue 番号）。
  追跡可能にして**孤児 TODO を防ぐ**。純粋 WIP/一時の未了のみ `# TODO:`（無印）可＝ただしコミット境界で解消 or 昇格。
- **マーカーは行コメントに置く**（docstring 内の散文 `（TODO）` は grep に乗らない＝不可）。
- **種別**: `TODO`=未了 / `FIXME`=既知の不具合 / `HACK`=意図的回避。
- **sweep は `make grep-todo`（R5）**で拾う（raw grep からの置換）。
- 監査: `make grep-todo` で「id リンクの有無・id が backlog に実在するか（孤児）・docstring 散文マーカー」を検出。

### 3) unit-quality リファレンス雛形（Makefile ブロック）に target を追記
```makefile
# 作業マーカー（TODO/FIXME/HACK）を拾う（R16）。CI を割らないよう grep の非ヒットでも exit 0。
.PHONY: grep-todo
grep-todo:
	@grep -rnE 'TODO|FIXME|HACK' $(SRC) --include='*.py' || true
```
`.PHONY` 行にも `grep-todo` を足す。

### 4) role-supervisor_or_worker（消費側・参照のみ）
roll-up 手順に「各 worker で `make grep-todo` を叩いて未了作業を俯瞰に上げてよい（規約の正本は
[[unit-quality]] R16/R5。supervisor は再定義しない）」を 1 行追加。**規約そのものは持たせない**（俯瞰スキルはフロー）。

## worker 側で実施済み（参考）
- manystore の Makefile に `make grep-todo` を materialize（上記 3 のレシピ）。
- manystore 自身の既存マーカーを backlog-id 書式へ整合（loadbalancer→M040 等）。詳細は manystore progress.md。
