---
from: manystore
role: worker
type: escalation
date: 2026-06-27
source: ユーザー対話（2026-06-27）「残タスク提示は識別子＋一行概要で。スキルで定義しておきたい」
---

## 上げたいこと
ユーザーから「**残タスク（バックログ）を提示するときは、識別子（M-row 等）＋一行程度の概要で列挙してほしい**」
という提示規約の要望。長い備考・設計詳細は畳み、一覧でスキャンできる形にする。ユーザーは**これをスキルとして
定義しておきたい**意向。

これは flow（記憶/作業フロー）の**提示レイヤの規約**であり、置き場として自然なのは `flow-memory-bank` スキル
本体（例: progress.md の「残作業」をユーザーへ提示する際の書式ルールを 1 文追記）。ただし flow は**親正本**で
worker からは編集不可（worker-boundary-guard 発火）。よって supervisor に昇格判断を仰ぐ。

- **案**: flow スキルの「コアワークフロー / ドキュメント更新」あたりに「残タスクをユーザー提示する際は
  *識別子＋一行* で列挙し詳細は progress.md 備考に畳む」を 1 行追加。
- worker 側は即運用（memory feedback `task-list-format` として記録済・本セッションから適用）。
- 置き場（flow R 項か、提示は role 側か）は supervisor 確定で。TODO 規約・反省 metrics と同じ「worker で
  materialize → 効けば親正本へ昇格」の流儀に乗せられる。
