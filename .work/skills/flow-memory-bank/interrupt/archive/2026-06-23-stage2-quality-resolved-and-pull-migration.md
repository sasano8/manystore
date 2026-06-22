---
from: dotfiles
role: supervisor
type: info
priority: low
date: 2026-06-22
---

## 共有（info）: あなたの quality エスカレは解決済み

以前あなたが親へ上げた「quality が常時ループ（memory-bank）から参照されず発揮されない」構造ギャップは、
dotfiles 側の **M004 Stage2** で解消しました:

- flow（旧 memory-bank）に **開発内ループ**を明文化（開発→自己点検＝flow→[[unit-quality]]→コミット単位に
  達するまで反復→commit→記録→次）。自己点検は flow から unit-quality を参照して走る＝quality が毎サイクル発揮される。
- quality は **flow→unit の自己点検 1 本**（両建て廃止）。supervisor は横断監査せず「自己点検せよ」と下ろすだけ。

→ あなたの懸念は構造として埋まったので、以後は通常サイクルで自己点検が走ります。

## 規約追従の進捗

1. **データ slot の移行**: `.work/skills/memory-bank/` → `.work/skills/flow-memory-bank/`
   → ✅ **完了（2026-06-22、supervisor が `git mv` で実施）**。UI config の featured パス
   （README / examples/manystore-ui*.toml の `skills/.../interrupt`）も flow-memory-bank に更新済み。

残り（priority: low — UI 開発を優先・急がない。次に Memory Bank を触るときで構いません）:

2. **上りエスカレを pull 型へ**: 親の interrupt に push するのをやめ、自分の
   `.work/skills/flow-memory-bank/outbox/` に 1 件 1 ファイルで積むだけにする（worker は親を知らなくてよい＝
   supervisor が `workers_dir` 走査で回収する）。activeContext 等の「manystore→dotfiles interrupt エスカレ」など
   push 前提の記述も pull 型に更新。
3. **スキル参照名を層エイリアスに統一**: `[[flow]]` / `[[role]]` / `[[unit-quality]]`。

## 背景 / 受け入れ条件

- 役割の権限は非対称（下り許可・上り禁止）。worker は親の正本・受信箱に直接書かない＝上りは outbox の pull のみ。
- 残り 2〜3 は次に Memory Bank を更新する機会に反映（単独タスクとして急がない）。
- 反映後は flow の開発内ループに従い 1 コミットで完了。
