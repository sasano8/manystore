# Product Context

## なぜ存在するか / 解決する課題

アプリが「どこに保存するか」を実装に直書きすると、ローカル開発・オブジェクトストレージ
（S3）・メッセージング系ストア（NATS Object Store）の切り替えが難しくなる。manystore はこれを
**共通インターフェースの背後に隠し、backend を差し替えるだけ**で済むようにする。

ストレージ抽象を独立ライブラリにすることで、利用側を肥大化させず、ストレージ層を単体でテスト・進化させられる。

## どう動くべきか

- 利用者は `KeyValueStore`（put/get/list/exists/delete/cp/mv）または `FileStore`（`open`→`FileObject`）に
  対してプログラムし、backend（local/s3/nats）は接続時に選ぶだけ。
- ラッパは 1 枚（`Safe*`）に留め、その下で backend を入れ替える。ラッパのネストはしない
  （性能低下＋利用者ごとに挙動が割れるため）。
- 書き込みは all-or-nothing（local は temp+`os.replace`、s3/nats は元々アトミック）。

## UX / 利用者ゴール

- backend 差し替えが「接続情報の違い」だけで完結する。
- async が一次。sync しか使えない文脈には `AsyncToSyncKeyValueStore` ブリッジで対応。
- パス traversal などの危険を `validate_safe_path` / `Safe*` ラッパが既定で防ぐ。
