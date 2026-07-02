URL スキーマ / config 復元 / プラグイン / k8s secrets（2026-07-02・対話で受領）

ユーザー要望（feasibility + 推奨順序を問う形で受領。実装は未合意＝doc-first で設計提示してから）:

1. fsspec 風の名前スキーマでストア取得。例 `s3://xxxx`, `nats://`, `local://.`, `manystore://xxxx`。
2. どの backend もバケット指定済みの粒度に統一。local はルートディレクトリを必ず指定（既定＝カレント）。
3. 構成ファイルからストア構成を復元。`manystore store init` で定義ファイルを生成。
   local はその構成ファイルのあるディレクトリを基準にストアのパスを解決。
4. manystore を kubernetes の secrets ストアとして連携（k8s Secret を backend 化）。
5. プラグインでストア追加をサポート（backend レジストリ + entry-points）。

現状の関連実装:
- 生成入口＝`storage/backends/__init__.py` の `create_unsafe_{key_value,file}_store(backend, **opts)`（if/elif ハードコード）。
- 接続＝`storage/connect.py` `connect_key_value_store(backend, **opts)` / `connecting`。
- config＝`serving/services/config.py`（`[contexts.<name>]` backend+opts, `root`→`local_dir` 正規化）は
  serving 専用。client 側「config からストア復元」は未実装。
- `manystore://` の実体＝`client/remote.py`（RemoteKeyValueStore / ManystoreClient）。

論点（回答で提示済み）: 1/2/3/5 は core Protocol を触らない「surface/factory/tool」の追加＝最小・汎用の IF を汚さない。
4 は新 backend＝conformancer マトリクスへ能力宣言（K8s は任意キー不可・streaming 不可・size 上限、ただし
resourceVersion で CAS 可）。推奨順序＝5(レジストリ)→1+2(URL/bucket)→3(config init/discovery)→4(k8s プラグイン)。
