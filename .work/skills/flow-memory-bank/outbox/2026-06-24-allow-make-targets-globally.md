---
from: manystore
role: worker
type: escalation
date: 2026-06-24
source: interrupt/archive/2026-06-24-aaa.md（要求1）
---

## 上げたいこと

ユーザー要望（interrupt 経由・本人が「スーパーバイザーへの要求」と明記）:

**`make format` / `make check` / `make test` / `make lint` をグローバルに許可したい。**

- 意図: これらの検証コマンドは worker フローで毎サイクル叩くため、毎回の permission prompt を
  なくしたい。グローバル（user 設定）で許可してほしい。
- 横断性: 単一 repo の設定ではなく**全 worker 共通の足回り**＝supervisor 層／グローバル
  settings.json の permissions で扱うのが妥当（だから worker ローカルではなく escalation）。
- 補足: 実体は Claude Code の permissions（`Bash(make format:*)` 等のグローバル allow）。
  worker 側 `.claude/settings.json` でも部分対応は可能だが、要望は「グローバル」。
