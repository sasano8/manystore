# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

**アクティブな作業なし**（2026-06-25 の protocols.py 集約＝コミット済で一段落）。次サイクルで
`progress.md`「残作業」から選定する（候補は下記「次のステップ」）。

## 直近の変更

- **protocols.py 集約 完了（2026-06-25・コミット `ece844b`）**: `stores/base.py` を削除し、既定実装
  （`FileStoreBase`/`KeyValueStoreBase`・アダプタ `KeyValueFileStore`/`KeyValueFromFileStore`・共有ヘルパ）を
  protocols.py へ全面集約。全 import を `..protocols` へ向け替え、`KeyValueFileStore` を `file.py` 公開 API に追加。
  `make test` 失敗の原因は test_storage が Protocol `AsyncKeyValueStore` を adapter として instantiate していた
  こと＝`KeyValueFileStore` に修正。詳細は systemPatterns「コア」へ昇格済。
- **memory clean 実施（2026-06-25）**: progress の完了行を畳み込み・解決済み「既知の問題」を GC・plan ファイルを
  `plans/` へ集約（m028 は完了につき削除）・完了 interrupt を archive。
- **バックログ4件を解決（2026-06-25）**: M036（error-swallow を fail-loud 化＝nats/s3 の exists・iter_all。test +3）・
  M033（limit 統一は波及済と確認＝コード変更なし）・M017（3.14+ で確定＝3.10+ 拡張は見送り）・M024（pull 型の文書追従）。

## 次のステップ

- 実装サイクル候補（`progress.md` 残作業から）: フェーズ2 `kv/json`／フェーズ3 `storage/manystore`／
  M032（Safe 包装込みファクトリ）／M034（conformance spec 表出力）／M010（local 非ブロッキング化）。

## 進行中の決定・考慮事項

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
