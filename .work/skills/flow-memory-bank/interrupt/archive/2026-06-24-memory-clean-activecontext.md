---
from: dotfiles
role: supervisor
type: instruction
priority: high
date: 2026-06-24
---

## 指示

`memory clean`（畳み込み/GC）を実行し、特に **activeContext.md を圧縮**せよ。

- 現状 `activeContext.md` が 552 行 / 61KB、`progress.md` が 46KB まで肥大。
- `activeContext.md` 内に「（旧フォーカス）」セクションが 13 個以上積もっている。
  これらは追記で残った過去履歴で、本来 `activeContext` に置くべきでない。
- `activeContext.md` は「**今の焦点のスナップショット**」に戻す＝残すのは
  「現在のフォーカス / 直近の変更 / 進行中の決定 / 次のステップ」のみ。
- 「（旧フォーカス）」群は `progress.md` に**完了マイルストーンとして簡潔に畳み込む**
  （既に progress に在るものは activeContext 側から削除するだけでよい）。

## 背景 / 受け入れ条件

- 背景: flow は毎タスク開始時に 6 コアファイルを全読み込みする。activeContext+progress
  で ~107KB を毎サイクル注入しており、worker の作業が体感で遅くなっている（実装遅延の主因）。
- 受け入れ条件:
  - `activeContext.md` が現在の焦点中心の簡潔な状態に戻る（目安 大幅減）。
  - 落とした履歴の要点が `progress.md` に残り、情報が失われていない。
  - `make check` 等は不要（ドキュメントのみの変更）。
