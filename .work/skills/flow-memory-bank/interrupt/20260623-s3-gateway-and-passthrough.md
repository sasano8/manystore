---
from: dotfiles
role: supervisor
type: instruction
priority: normal
date: 2026-06-23
---

## 指示: S3 ゲートウェイ（manystore を S3 API で公開）＋ backend=s3 のパススルー

manystore を **S3 互換 API のサーバ（ゲートウェイ）として公開**できるようにする。クライアントは
S3 プロトコルで話し、その背後で任意の manystore backend（local / nats / s3）に読み書きが向く。

加えて、**backend がそのまま s3 のときはパススルーモード**を用意する：

- manystore がバイトを中継（プロキシ）せず、可能なら**実 S3 へ直接飛ばす**
  （presigned URL リダイレクト等で、データ平面は manystore を経由させない）。
- パススルー不可なケース（リダイレクト不可・署名条件が合わない 等）はプロキシにフォールバック。

## 背景 / 受け入れ条件

- manystore は既に `s3` backend を持つ。本件は「**backend を S3 として使う**」ではなく
  「**manystore 自体を S3 API で外に提供する**」ゲートウェイ層の追加。スコープ境界に注意
  （ストレージ抽象の最小・汎用を壊さない＝ゲートウェイは IF の上に薄く乗せる。projectbrief「最小・汎用に保つ」）。
- **まず flow の開発内ループで設計を回すこと**（deep think の着手前ゲート）。確定すべき設計論点:
  - 対応する S3 操作の最小集合（GET/PUT/HEAD/LIST/DELETE/multipart の要否＝YAGNI で絞る）。
  - パススルーの実現手段（presigned redirect か、リダイレクト不可時のプロキシfフォールバックか）と、
    その時の認証・署名の扱い（クライアント資格情報をどう実 S3 署名に橋渡しするか）。
  - サーバ実装の置き場（manystore 本体に取り込むか、別エントリ/extra か＝独立ライブラリの肥大回避）。
- 受け入れ: ゲートウェイ経由で代表 backend に対し GET/PUT/LIST が通り、backend=s3 でパススルー
  （またはフォールバック）が観測できること。`uv run pytest` 緑。実 backend（SeaweedFS 等）で疎通確認。
- 急がない（UI 開発 M019/M020 を止めてまで割り込ませなくてよい）。**設計を先に固めてから着手**。

## 優先度メモ（supervisor）

- 実効 priority: **normal**。新機能で要設計のため、まず設計（deep think 着手前ゲート）→ 輪郭が出てから実装。
- タスク2（パススルー）はタスク1（ゲートウェイ）の **optional モード**として 1 機能に束ねた。
  パススルーは "できれば" の要望なので、ゲートウェイ本体が動けば段階的でよい。
