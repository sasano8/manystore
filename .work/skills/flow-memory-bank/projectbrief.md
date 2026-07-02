# Project Brief: manystore

## 概要 / スコープ

**manystore** は **ストレージ抽象ライブラリ**。`local` / `nats` / `s3` の各バックエンドを共通の
インターフェース（`KeyValueStore` / `FileStore`）の背後に隠し、利用側は backend を差し替えるだけで
保存先を切り替えられる独立ライブラリ。

**スコープ:** ストレージ抽象とその backend 実装・接続・安全パス・合成ストアまで。これを超える利用側
固有の要求は manystore 本体に取り込まず、利用側の adapter で吸収する（下記コア要件参照）。

**メインは「ファイルストレージ（file/value 抽象）」**。put/get でファイル＝値を出し入れするのが本質で、
戻り値も *file ドメインのメタデータ*（`FileInfo` 等）に留める。

## 非ターゲット（パラダイム）

- **request/response 型は対象外**: backend の生レス（s3 dict / nats revision / httpx.Response）を封筒に
  包んで返す `StoreResponse`/`put_response` 系は**却下**（2026-06-26）。file/value 抽象に transport の
  response を持ち込むのはパラダイム不一致。生レスの中身は共通化不能ゆえ表に出さない。
- **pub/sub 型も対象外**: 購読・イベント配信は manystore のモデルではない。
- ただし file/value の枠内に収まる列挙・絞り込み（例: `iter_all(prefix=…)`）は core に足してよい。
  prefix は当初 optional capability だったが、2026-06-26 に **core 引数へ畳んで capability を廃止**した
  （progress.md「意思決定の変遷」参照）。

## コア要件

- 共通 IF（`KeyValueStore` / `FileStore`）で local / nats / s3 を差し替え可能にする。
- async を一次実装とし、sync ブリッジを提供する。
- 接続ライフサイクル（connect / retry / timeout / deadline）と安全パス検証を備える。
- **最小・汎用に保つ**：利用側都合で IF を安易に拡張しない（最小プリミティブから合成。YAGNI）。

## ゴール

- 利用側が backend を adapter 経由で差し替えて使えること（結線は利用側に閉じる）。
- 単体で `uv run pytest` が緑（独立ライブラリとして自己完結）。
- 実 backend（SeaweedFS / 実 NATS）での疎通まで検証できること。

## 北極星: conformance = 仕様の単一源泉（究極の仕様駆動）

**理想（2026-06-28・ユーザー方針）**＝ストア実装の「仕様（守るべき挙動契約）」を **conformancer に集約**し、
そこから 4 つの価値を同時に得られる状態を目指す:

1. **テストできる**: 各契約が `assert_*`／run_light·middle·heavy で実行可能（実装漏れを backend 横断で機械検知）。
2. **pytest-cov で認識できる**: 契約の網羅状況・未到達がカバレッジに現れる（仕様の充足度を定量把握）。
3. **仕様書として出力できる**: 契約カタログから `docs/*_spec.md` を生成（M034 の存在チェック spec を挙動契約へ拡張）。
4. **スキャフォールドの材料になる**: 新 backend は契約一覧から雛形を起こし、conformancer を通すだけで
   実装漏れが loud に落ちる（契約が実装の TODO リストになる＝仕様駆動の出発点）。

これにより **究極のテスト駆動／仕様駆動**（仕様を書く＝テストが生まれ＝カバレッジに出て＝ドキュメント化され＝
雛形になる）を実現する。実装は段階的（M065＝run_middle ＋ 絶対契約 ＋ fault-injection から着手）。
