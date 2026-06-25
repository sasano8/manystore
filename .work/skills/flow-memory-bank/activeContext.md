# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

**アクティブな作業なし**（2026-06-25 に M034〔conformance spec を docs 出力〕＋ GitHub Pages CI 完了＝コミット
`ec70306`。続けて memory clean を実施＝interrupt/archive 全 GC・完了 plan m019 削除・グローバル memo の旧スキル名修正）。
次サイクルで `progress.md`「残作業」から選定する（候補は下記「次のステップ」）。

## 直近の変更

> 完了マイルストーンの詳細は `progress.md` に集約。ここには溜めない（重複は memory clean で畳む）。直近は上記
> 「現在のフォーカス」を参照。

## 次のステップ

- 実装サイクル候補（`progress.md` 残作業から）: **M011（安全入口の最終形）**＝下記「進行中の決定」の命名/格下げ/
  Array enter_context を実装／フェーズ2 `kv/json`／フェーズ3 `storage/manystore`／M010。

## 進行中の決定・考慮事項

- **【決定・未実装】安全入口の最終形（M011・顔だけ残し生は格下げ）**: ①命名＝`open_async_{kv,file,array}_store`（顔・
  safe・接続CM）＋ `create_safe_{kv,file,array}_store`（safe・構築のみ）。`create_unsafe_*`・生クラスはトップ `__all__`
  から外し `storage.backends` へ格下げ。②`ArrayKeyValueStore` は mount を登録のみ（同期・I/O なし）に分離し、接続は
  `open_async_array_store` の enter_context CM へ（現状 mount が connect も担う二重責務を解消）。`StorageService` は追従。
  ※外向き API 変更なので着手時に粒度を再確認。
- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない（YAGNI）。
- **worker/supervisor**: 本 repo は dotfiles（`workers_dir: workers`）配下の worker。下り=interrupt 投函／
  上り=`outbox/` へ pull 型エスカレ（親は直接知らない）。
- **MB 運用**: Cline 準拠 6 コア＋`plans/`（完了 plan は削除・残フェーズの plan のみ保持）。`.work/` は commit、
  コミットは「切りのいいところ」でコード＋MB を 1 コミット、`agent` ブランチ単線、push は明示時のみ。

## 重要なパターン・好み / 学び

- **設計原則の正本は repo の `docs/architecture.md`**（FileStore=KVS+IO・核は native primitive 側・conformance）。
  Memory Bank は一時記憶ゆえ要約のみ。
- **フローは全て interrupt を介す＋参照系は reference/**: 対話の作業要望も着手前に interrupt へ書き出してから取り込む。
- **品質チェックは `make` 経由**（`make format`/`make test`＝fast・`make test-all`＝全部）。ベタ書き `uvx ruff …` 禁止。
  ※`make test` は lint を回さない＝format ドリフトは別途 `make format` で検出（過去に CJK 行の E501 が埋もれた）。
- **3.14 前提で `from __future__ import annotations` は全廃**（PEP 649 で前方参照は valid）。新規ファイルにも入れない。
- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる（過去バグ）。
- KV=バッファ概念／FileStore=バッファ無し概念。真の streaming はクライアント wrap で得る。
