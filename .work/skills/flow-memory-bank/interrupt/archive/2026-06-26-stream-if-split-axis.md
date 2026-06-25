# stream IF の分割軸（M026 設計入力・対話発）

2026-06-26 対話。「ストリーム型 IF を2種に切るべきか」への意見すり合わせ結果。M026 に畳み込み。

## 論点

ストリーム型には (A)「連続垂れ流し」(jsonl 等＝片方向 fire-and-forget) と (B)「送信単位に一塊があり
単位ごとに応答(ステータス)が返る」(pub/sub 的) がある。別種として切るべきか？

## 結論

**切るべき。ただし軸は「jsonl vs pub/sub」ではなく「送信単位ごとに応答チャネルがあるか否か」の1点。**

- (A) と (B) は2つの直交軸を混ぜている:
  - **フレーミング**（バイト連続 / レコード境界 jsonl）＝単なる符号化差。型を割る理由にならない。
  - **応答の向き**（片方向 / 単位ごとに応答）＝**本質。型・エラー・相関が全部変わる**。
  - jsonl が垂れ流しに見えるのは応答が無いからで、レコード境界の有無は本質でない。
- **切る決定打＝エラーモデル非互換**:
  - (A) エラー＝ストリームの死（失敗モード1つ）。`write(x)->None` / `AsyncIterable[T]`。
  - (B) エラー＝1単位ごとのステータス（失敗はデータ・ストリームは生存）。`request(x)->Response[R]`＋相関ID。
  - 統一すると必ず片方が歪む（A に偽応答を捏造 / B の応答を握り潰す）。
  - (B) は「データのストリーム」でなく **1 接続に多重化された N 個の request-response＝トランザクションのストリーム**。

## manystore への落とし込み（カテゴリ差）

- **(A)＝ストレージ/IO の関心事**。`FileStore` の `open_reader`/`open_writer`（方向別・バッファ無し・原則6）に乗る。
  **コアに属する**。M026 の MVP=byte stream はこちら。
- **(B)＝メッセージング/トランスポートの関心事**。ストレージ抽象ではない。projectbrief スコープ＋原則1/5(YAGNI)＋
  原則6/M026「真の streaming は client wrap・HTTP 公開は buffered」に照らし、**コア IF に入れず `client/` か利用側
  adapter に置く**（仮称 `Exchange`/`RPC`＝`call(req)->resp`。storage Protocol には載せない）。
- 中間「reliable one-way（配送 ack はあるが app 応答なし＝JetStream ack 的）」は (A) 側の*信頼性*オプション。
  (B) と混同しない（混ぜると軸が再び斜めになる）。

## M026 への含意

stream IF を設計する際、**`StreamStore`（無境界・片方向 append/follow）と request-response 型を同一抽象に載せない**。
後者が要件化したら別レイヤ（client）として doc-first で別途合意する。
