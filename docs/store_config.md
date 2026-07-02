# 構成ファイルからストアを復元（`manystore.toml`）

ストア構成を **`manystore.toml`** に宣言し、名前（context）でストアを開く。URL 直指定
（[open_store](url_scheme.md)）が「その場の 1 本」なのに対し、構成ファイルは「名前付きストア群を
プロジェクトに固定」する（`open_store("mycontext")`）。

## 生成（`manystore store init`）

```console
$ manystore store init          # cwd に manystore.toml を生成（--force で上書き）
$ manystore store init path/to  # 生成先を指定
# `python -m manystore store init …` でも同じ（console script が無い環境向け）
```

CLI は Typer 製（`manystore --help` でサブコマンド一覧）。統合サーバは `manystore serve --config <toml>`
（旧 `python -m manystore --config <toml>` も後方互換で serve に振られる）。

生成される雛形（抜粋）:

```toml
default_context = "default"

[contexts.default]
backend = "local"
root = "."          # このファイルのディレクトリ

# [contexts.bucket]
# backend = "s3"
# s3_bucket = "my-bucket"
# s3_endpoint = "http://127.0.0.1:9000"
# writable = false
```

- `[contexts.<name>]` … context（名前付きストア）。`backend` ＋ backend 固有 opts（[registry] の
  factory へ渡る flat kwargs＝`s3_bucket=` など）。`writable=false` で読み取り専用宣言。
- `default_context` … 名前を省略したときに開く context。

## 名前で開く（`open_store("ctx")`）

```python
from manystore import open_store

async with open_store("default") as store:   # 構成ファイルを discovery して context を解決
    await store.put("k", b"v")

async with open_store("") as store:           # 空文字 = default_context
    ...
```

- `target` に `://` が無ければ **context 名**として扱い、**`manystore.toml` を上方向 discovery**
  （cwd から親へ辿る＝git/pyproject 風）して解決する。`config=` で明示的に [StoreConfig] を渡せる。
- **local の相対 `root` は構成ファイルのディレクトリ基準**で絶対化される（cwd 非依存＝どこから実行しても
  同じ場所を指す）。絶対 `root` はそのまま。
- URL 直指定（`open_store("s3://…")`）と同じ入口＝どちらも Safe 包装＋接続 CM。

## API

```python
open_store(target, *, verify=True, policy=None, config=None)  # URL or context 名（上記）
load_store_config(path) -> StoreConfig                         # 1 ファイルを読む（base_dir=そのdir）
discover_store_config(start=None) -> StoreConfig | None        # 上方向 discovery（無ければ None）
find_config_file(start=None) -> Path | None                    # 構成ファイルの場所だけ探す
```

`StoreConfig` は `contexts: {name: ContextConfig}` / `default_context` / `base_dir`（相対解決基準）を持つ。

## serving との関係

HTTP 公開の serving 層（`manystore serve --config <toml>`）も**同じ context 定義**を使う
（`[[views.featured]]` などの UI 重点設定が加わるだけ）。context のパースと local 相対解決は neutral な
`storage/config.py` を serving/client 双方が再利用する（構成の二重持ち＝drift を避ける）。
