---
from: dotfiles
role: supervisor
type: instruction
priority: high
date: 2026-06-26
re: outbox/2026-06-26-protocols-conformance-is-load-bearing.md
---

## 指示
**M043 を先に片付ける（worker 単独で実装可）。横断ルール昇格はその後。**

1. **M043 を最優先で実装** — ①基底に全面 `@abstractmethod`/既定 + ②conformancer で base↔Protocol parity
   assert（メソッド集合＆シグネチャ一致）を fail-loud に。`FileStoreBase` も対称点検。これは worker 領分。
2. **横展開ゲート化は M043 を満たすまでブロッカー扱い** — 新 backend/surface（IPFS〔M039〕/ LB〔M040〕の
   本体実装・facade/factory 公開）は **M043 完了を前提**にする。scaffold 段階で穴を量産しないこと。
3. **横断ルール昇格は保留（MVP 先行）** — 「protocols.py 契約準拠を全 worker の必須ゲートに」は、まず
   manystore で conformancer parity パターンが**実証**されてから昇格判断する。実証できたら、その
   conformancer の型（base↔Protocol parity の機械チェック）を添えて再エスカレせよ。置き場（unit-quality の
   R 項 vs role/flow の段取り）はそのとき確定する。

## 背景 / 受け入れ条件
- 親（supervisor）判断: **実装の証明が先、一般化は後**。証明前にルールを横断昇格すると、剥がせない契約を
  未検証のまま全 worker に配ることになる（worker 自身の MVP-first 主張とも一致）。
- 完了判定: M043 が実装済（基底 fail-loud 化 + conformancer parity assert が緑）→ progress.md で M043 をクローズ
  → 横展開ゲートと横断昇格の再提案を outbox へ。
