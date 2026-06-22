---
from: dotfiles
role: supervisor
type: instruction
priority: normal
date: 2026-06-23
---

## 指示: ストレージ適合性テスト（conformance test suite）の実装

ストレージを**共通化**するための土台として、共通 IF（`KeyValueStore` / `FileStore`）に対する
**適合性テスト群（conformance / contract test suite）**を実装する。任意のストレージ実装（local / nats / s3
だけでなく、外部の第三者実装も含む）が、このテスト群を**自分の backend に当てるだけで「共通 IF に準拠しているか」を
証明できる**形にする。

要点（共通化＝「テストが契約の正本」になる）:

- backend 非依存の振る舞い契約をテストとして固定する（put/get/list/exists/delete/cp/mv、`FileStore.open`→
  `FileObject`、all-or-nothing 書き込み、安全パス検証、async/sync ブリッジ等）。
- **再利用可能な形**で公開する＝「接続情報を渡すと、その backend に対して契約テスト一式が走る」
  パラメタライズされたスイート（fixture/抽象基底クラス/プラグイン等、実装手段は設計で決める）。
- これは後述のオープンテスト“プラットフォーム”（dotfiles 側 M010）とは**別タスク**。本件は
  「テストの中身（契約）」を manystore に実装するところまで。プラットフォーム（トンネル/エージェント/配送）は持ち込まない。

## 背景 / 受け入れ条件

- 既存テスト（`tests/`）との関係を整理：現状の backend テストを契約スイートとして**抽出・共通化**できるか、
  新規に契約レイヤを切るか。重複を増やさない（最小・汎用＝projectbrief）。
- **flow の開発内ループで設計を先に**（deep think 着手前ゲート）。確定論点:
  - スイートの再利用形（外部 backend が import して自分の接続情報で回せる API 形状）。
  - パラメタ化の単位（backend factory / 接続設定の注入の仕方）。
  - 「契約」の境界（どこまでを必須準拠とし、どこを optional とするか）。
- 受け入れ: local / nats / s3 の 3 backend で同一の契約スイートが緑（`uv run pytest`）。
  外部実装が接続情報を差すだけで回せる形になっていること。実 backend（SeaweedFS / 実 NATS）で疎通。
- 優先度 normal。UI（M019/M020）を止めるほど急がない。設計先行。
