# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `docs/architecture.md` / `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

**未着手＝次サイクルを候補から選定**。直前に **M071（公開 1 Store 統合）完遂**（2026-07-03・Stage 1〜6＋語彙 purge）＝
公開面は完全に 1-Store（`AsyncStore`＝値 API `put/get…` ＋ IO API `open_*`／put/get だけ見る view は `AsyncBufferedStore`）。

次サイクル候補:
- **M051** — k8s secrets backend（当初 5 要望の最後）
- **M076** — nats-fake を conformance provider 化（JetStream メタ subject 忠実化）
- **M078** — 横断関心の合成層（doc-first 起案済＝`plans/m078-composition-layers-plan.md`。op-middleware / codec / auth の 3 系統・未決 5 点）
- 機能・完成度 — M013（メタ/content-type）/ M012（pagination）/ M021 残（S3 GW）/ M025 残（名前空間 P2/P3）

> 北極星＝conformance を仕様の単一源泉に（projectbrief）。①テスト可能 ②cov 可視 ③spec 生成 ④scaffold まで完備
> （M065/M066）。全契約は `tests/conformance_providers.py` に宣言し `tests/test_conformance_matrix.py` が全 provider
> （dict/local/remote/実 nats/s3）へ非破壊で流す。

## 直近の変更

> 完了マイルストーンの詳細は `progress.md`（M-row）に集約。ここには最近の数件だけ残す。

- **M071 完遂（2026-07-03）＝公開を 1 Store に統合し旧「KeyValueStore/FileStore」語彙を言語レベルまで一掃**。
  facade（kv/file）削除・backend/合成/アダプタ/生 factory の旧名を **shim なし撤去**・conformancer 改名・
  docstring/手書き docs/README/examples/生成 docs を 1-Store モデルへ。make check 緑（268）・test-heavy 緑（44・実 backend）・
  mkdocs --strict 緑。経緯・段階の詳細は **progress M071 行**。
- **M078 の doc-first 設計を起草**（2026-07-03）＝`plans/m078-composition-layers-plan.md`（合成層は shape 差で 3 系統に
  割る＝op-middleware / codec〔crypto.py 土台〕/ auth〔with_auth 束縛 view・authz はスコープ外候補〕）。未実装。

## 次のステップ

- 次サイクル未選定（上記候補から）。M078 着手なら doc-first の未決 5 点（`plans/m078…`）を先に詰める。

## 進行中の決定・考慮事項

- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない（YAGNI）。拡張は doc-first 合意。
- **worker/supervisor**: 本 repo は dotfiles（`workers_dir: workers`）配下の worker。下り=interrupt 投函／
  上り=`outbox/` へ pull 型エスカレ（親は直接知らない）。
- **MB 運用**: Cline 準拠 6 コア＋`plans/`（完了 plan は削除・残フェーズの plan のみ保持）。`.work/` は commit、
  コミットは「切りのいいところ」でコード＋MB を 1 コミット、`agent` ブランチ単線、push は明示時のみ。

## 重要なパターン・好み / 学び

- **設計原則の正本は repo の `docs/architecture.md`**（1 つの Store＝値 API+IO API・核は native primitive 側・
  値寄り/IO 寄り/両軸 native の 3 基底・conformance）。Memory Bank は一時記憶ゆえ要約のみ。
- **フローは全て interrupt を介す＋参照系は reference/**: 対話の作業要望も着手前に interrupt へ書き出してから取り込む。
- **品質チェックは `make` 経由**（`make format`/`make test`＝fast・`make test-all`＝全部）。ベタ書き `uvx ruff …` 禁止。
  ※`make test`（fast）は lint を回さない＝format ドリフト（特に CJK 行の E501）は別途 `make format` で検出。
- **語彙/API の一斉 purge は最初にリポジトリ全体を grep**（src だけでなく README/examples/生成物/テスト名まで）。
  M071 で src 偏重の purge が README の公開サンプルを壊したまま残し、後からユーザー指摘で発覚した（教訓）。
- **3.14 前提で `from __future__ import annotations` は全廃**（PEP 649 で前方参照は valid）。新規ファイルにも入れない。
- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる（過去バグ）。
- **バッファ性は IF の本質**＝値 API（put/get）は buffered 概念・IO API（open_*）は streaming 概念。native がどちら寄りかを
  `BufferedStoreBase`/`StreamingStoreBase`/`StreamableBufferedStoreBase` で型に出す。真の streaming は client wrap で得る。
- **⚠️作業環境の異常（再発・要ユーザー報告）**＝`except (A, B):` が py2 構文 `except A, B:`（SyntaxError）へ
  外部から書き戻される（原因不明・hook/インジェクション疑い）。**回避＝複数例外の括弧 catch を避け単一クラス
  catch**（`except JSNotFound:` 等）。M046/M054 で再発。
