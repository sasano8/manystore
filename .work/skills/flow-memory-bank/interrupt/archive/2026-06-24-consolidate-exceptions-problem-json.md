# 要望（対話・2026-06-24）

> exceptions に例外をまとめたい。
> 例外は application/json+problem に変換できるメソッドを用意しておく。

## トリアージ（2026-06-24）

- 既存バックログ **M009（統一例外階層 ManystoreError）** に対応する要望＝M009 を着手・具体化。
- 宛先 = **いま着手**（実装）。`manystore/exceptions.py` に 4 例外を集約＋基底 `ManystoreError`＋
  `to_problem()`（RFC 9457 `application/problem+json`）を用意。元モジュールは再エクスポートで後方互換。
- メディアタイプは正しくは `application/problem+json`（要望の "application/json+problem" は表記ゆれ）。
- HTTP ルートの応答形式は今回変えない（メソッドを「用意しておく」までがスコープ。routes 採用は follow-up）。
