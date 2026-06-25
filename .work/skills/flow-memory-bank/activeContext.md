# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

**アクティブな作業なし**（2026-06-26 に M011〔安全入口の最終形〕完了＝2 コミット：C1 Array 責務分離＋C2 命名
マトリクス確定。詳細は `progress.md` M011）。次サイクルで `progress.md`「残作業」から選定する。
（前段: 2026-06-25 M010〔local backend 非ブロッキング化＝anyio オフロード〕。）

## 直近の変更

> 完了マイルストーンの詳細は `progress.md` に集約。ここには溜めない（重複は memory clean で畳む）。

- interrupt `2026-06-25-m010-async-file-lib.md` を取り込み＝既存 backlog M010 の方式論点を精緻化。`aiofile`（真 async）
  より `anyio`（スレッドプール系・在中）を採用（理由: buffered では native AIO もスレッド fallback・移植性/最小優先）。
  → archive へ退避済。M010 を実装・完了。

## 次のステップ

- 実装サイクル候補（`progress.md` 残作業から）: **M011（安全入口の最終形）**＝下記「進行中の決定」の命名/格下げ/
  Array enter_context を実装／フェーズ2 `kv/json`／フェーズ3 `storage/manystore`。

## 進行中の決定・考慮事項

- **【完了】安全入口の最終形（M011）**: 入口の命名マトリクス（3×3）を確定＝**unsafe**（`create_unsafe_{key_value,file}_store`
  ＝生・未接続・キー検証なし／array は `ArrayKeyValueStore` 直）/ **safe**（`create_safe_{key_value,file,array}_store`
  ＝Safe 包装・未接続）/ **顔**（`open_async_{key_value,file,array}_store`＝Safe 包装＋接続 CM）。生口はトップ公開に残す
  （ユーザー確定＝格下げせず名前で明示のみ）。`ArrayKeyValueStore.mount`/`unmount` は登録のみ（同期）。
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
