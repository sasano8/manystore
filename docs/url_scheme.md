# 名前 URL でストアを開く（`open_store`）

fsspec 風に **1 本の URL 文字列**でストアを取得する。`scheme` が backend 名
（[registry](backend_registry.md)）に解決され、URL の各部が backend の接続情報に写される。

```python
from manystore import open_store

async with open_store("s3://mybucket?endpoint=http://localhost:9000") as store:
    await store.put("k", b"v")
```

`open_store(url)` は既存の顔 `open_async_key_value_store` の URL 版＝**Safe 包装＋接続 CM**
（キー検証つきの接続済みストアを yield し、終了で aclose）。URL を `(backend, opts)` に分解して
そこへ委譲するだけの薄いサーフェス（core Protocol 変更なし）。

## 文法

```
scheme://[netloc][/path][?query]
```

- **scheme** … backend 名（`s3` / `nats` / `local` / `memory` / `http` / `manystore` / plugin）。
- **netloc** … **bucket（＝ストアの粒度）**。全 backend「1 ストア = 1 bucket/root」に統一。
- **path** … （予約）bucket 内 prefix。M069 時点では local の root 指定にのみ使う（他は将来）。
- **query** … backend 固有の接続オプション（endpoint / region / 資格情報 / server URL / headers）。

### backend 別マッピング

| scheme | netloc | 主な query | 例 |
|--------|--------|-----------|-----|
| `memory` | — | — | `memory://` |
| `local` | root の一部 | — | `local://.`（cwd）/ `local:///abs/path` / `local://./data` |
| `s3` | bucket | `endpoint` `region` `access_key` `secret_key` `addressing_style` | `s3://bkt?endpoint=http://localhost:9000&region=us-east-1` |
| `nats` | bucket | `server`（NATS サーバ URL） | `nats://bkt?server=nats://localhost:4222` |
| `http` | ホスト | （`path` 込みで base_url） | `http://host/base`（read-only） |
| `manystore` | context(bucket) | `server`（manystore サーバの NS ルート） | `manystore://ctx?server=http://host/kv/raw` |

補足:
- **`local`**：`netloc`＋`path` を連結して root ディレクトリにする。`local://.` は **cwd**、
  `local:///abs` は絶対パス、`local://./rel` は cwd 相対。root は init で絶対パス固定（既存の Local 仕様）。
- **`nats` / `manystore`**：`netloc` は **bucket/context**（サーバ所在ではない）＝bucket 粒度を優先。
  サーバ所在は `?server=` で渡す（`nats://bkt?server=nats://host:4222`）。scheme の `nats` は「manystore の
  NATS backend」の意味で、NATS サーバ URL とは別レイヤ（混同を避けるため server は query に分離）。
- **`http`**：唯一の例外＝URL 全体が **base_url**（bucket 概念なし・read-only backend）。`netloc`＋`path`
  をそのまま base_url にする。
- **資格情報（`access_key`/`secret_key`）**：query で渡せるが**秘密を URL に置くとログ等に残る**。
  ローカル/開発向けの利便であり、本番は環境変数（boto の既定チェーン）や構成ファイル（M070）を推奨。
  s3 で未指定なら boto の既定資格情報チェーン（env/IAM）に委ねる。

## API

```python
open_store(url: str, *, verify: bool = True, policy: ConnectPolicy | None = None)
    -> async context manager[SafeKeyValueStore]   # Safe 包装＋接続

parse_store_url(url: str) -> tuple[str, dict[str, object]]   # (backend, opts) を返す純関数（試験/構成用）
```

`parse_store_url` は URL を `(backend, opts)` に分解するだけの純関数。`open_store` はこれを使って
`open_async_key_value_store(backend, **opts)` に委譲する。**scheme が無ければ構成ファイルの context 名**
として解決する（`open_store("mycontext")`＝[構成ファイルからストア復元](store_config.md)）。

## 非対象・後続（M069 スコープ）

- **opts は既存の flat kwargs 形**（`s3_bucket=`/`s3_endpoint=`…）へ写す＝既存 factory を無改修で使う
  （後方互換）。**backend ネイティブ opts（`bucket=`/`endpoint=`）への整理は別途**（M068 シム廃止の出口）。
- **path=prefix によるサブスコープ**（`s3://bkt/prefix/`）は prefix 前置ラッパが要るので後続。
- FileStore 版（`open_file_store(url)`）と名前解決（`open_store("ctx")`）は後続（M070 連動）。
