---
from: manystore
role: worker
type: escalation
date: 2026-06-26
source: manystore/protocols.py（AsyncKeyValueStore L55-68 / KeyValueStoreBase L186-207）, manystore progress.md M043
---

## 上げたいこと（最重要・横断ルール提案）

**`protocols.py` の契約は load-bearing な正本であり、横展開（新 backend／新 surface／他 worker repo への
このストレージ抽象パターンの適用）の前に「契約準拠」を機械的に強制すべき**。準拠せずに横展開すると、
後で多大な後戻り（全実装の手直し・公開 API の破壊的変更）が生じる——ユーザーが繰り返し強調している点。

### なぜ今上げるか（具体証拠＝drift が現に発生している）
manystore 内で既にズレを検出（worker backlog **M043**＝最重要に登録済）:
- `KeyValueStoreBase`(ABC) が `@abstractmethod` にしているのは `get_or_raise` だけ。
- `AsyncKeyValueStore`(Protocol) は put/iter_all/list_all/exists/delete/cp/mv/connect/aclose まで契約。
- → 基底を継承した実装は `get_or_raise` さえ書けば**インスタンス化が通り、残り 9 メソッド未実装でも黙って
  Protocol を破れる**（fail-loud でない）。「契約＋既定実装の唯一の源泉」原則が、その源泉内で崩れている。
- 横展開の実例（IPFS backend / loadbalancer surface の scaffold）が `KeyValueStoreBase` を継承し始めており、
  ここで準拠を担保しないと同じ穴が量産される。

### supervisor へのお願い（判断・横断ルール化）
1. **「protocols.py 契約準拠」を横展開の必須ゲートに**——新 backend/surface/worker は、契約の全面を満たすことを
   **機械チェック（conformancer の base↔Protocol parity・メソッド集合＆シグネチャ一致）で fail-loud に検証**してから
   横展開する、というルールの是非を判断してほしい。
2. **置き場の確定**——これは「お作法（規約）」なので [[unit-quality]] に R 項として持たせる案がある一方、
   「横展開の段取り」は俯瞰スキル側。定義（unit）と適用順（role/flow）の切り分けは supervisor 領分なので確定を仰ぐ。
3. **M043 を横展開のブロッカー扱いにするか**——manystore 側は M043（lockstep 保証）を最重要に積んだが、
   他 worker へ同パターンを配る前に M043 を片付ける前提にするか、優先度の確定をお願いする。

> worker（manystore）側で実装可能な是正（M043＝基底の fail-loud 化 + conformancer parity assert）は backlog 済。
> 本エスカレは「横断ルールとして昇格すべきか」という**メタ判断**を上げるもの（実装の指示待ちではない）。
