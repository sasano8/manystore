# 要望（対話・2026-06-24）

HTTP エンドポイント再設計（M025 改訂）。対話で確定した設計方針:

1. `contexts/{ctx}/objects/{key}` の `contexts`/`objects` 飾りは不要（M028 で context=ArrayStorage
   第一階層にしたので）。**先頭 = bucket、その後は全部 `{path}`（不透明）**＝`{bucket}/{path}` が
   そのまま ArrayStorage キー。native 表層語を `bucket` に統一（S3 と語彙を揃える）。
2. native HTTP API から **prefix を廃止**（フラット list-all のみ）。仮想フォルダ/末尾スラッシュ規則も不要に。
3. prefix は NATS 由来ではない（汎用 startswith フィルタ）。S3 互換のために要るので、**backend/ラッパーの
   optional capability（`SupportsPrefixListing` + 汎用フォールバックヘルパ）に移設**。S3 はネイティブ
   （list_objects_v2 Prefix）、他は総なめフォールバック。**Safe/Array ラッパーが委譲**して native 効率を
   伝播（M027b と同じ伝播パターン）。S3 ゲートウェイ ListObjectsV2・multipart 内部はこのヘルパ経由に置換。
4. WS イベント購読は同一パス（`WS NS/{bucket}/`）を upgrade で判別。
5. 破壊的変更（未リリース＝互換エイリアス無し）。

## トリアージ（2026-06-24）

- `m025-namespace-restructure-plan.md` を**改訂版に全面書き直し**（addressing を bucket/path 化・prefix 撤去）。
- 実装タスク：**M025改（addressing 再設計）** と **M030（prefix capability）** を progress に起票。
- doc-first＝計画確定までを今サイクルで commit。実装は次サイクル。
