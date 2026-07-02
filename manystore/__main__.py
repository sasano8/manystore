"""`python -m manystore` の CLI（サブコマンド）。

- `manystore serve --config <toml>` … 統合アプリ（native REST/WS ＋ S3 互換 GW）を起動（M023）。
  native REST/WS（`/kv/raw/...`＝buffered）と S3 互換ゲートウェイ（`/storage/s3/...`＝streaming）を
  1 プロセス・1 共有 [StorageService] で公開する。S3 クライアントは `endpoint_url=<host>/storage/s3`
  を向ける（path-style）。既定 bind は localhost。外部公開は明示的に `--host 0.0.0.0`。
- `manystore store init [dir]` … ストア構成ファイル `manystore.toml` の雛形を生成（M070）。

後方互換: 旧 `python -m manystore --config <toml>`（サブコマンド無し）は `serve` に振る。
単体起動（`python -m manystore.serving.server` / `python -m manystore.serving.gateway`）も利用可能。
"""

import argparse
from pathlib import Path

from .storage.config import CONFIG_FILENAME

# `manystore store init` が生成する雛形（local 相対パスはこのファイルのディレクトリ基準で解決）。
_INIT_TEMPLATE = """\
# manystore ストア構成（`manystore store init` で生成）。
# `open_store("<context>")` でここの context を名前解決して開ける。
# local の相対パスは**このファイルのあるディレクトリ基準**で解決される（cwd 非依存）。

default_context = "default"

[contexts.default]
backend = "local"
root = "."          # このファイルのディレクトリ

# 例: S3（資格情報は環境変数推奨。ここに直書きすると構成ファイルに残る）
# [contexts.bucket]
# backend = "s3"
# s3_bucket = "my-bucket"
# s3_endpoint = "http://127.0.0.1:9000"
# s3_addressing_style = "path"
# writable = false   # 読み取り専用にする context は writable=false
"""


def _cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .serving.combined import create_combined_app
    from .serving.services.config import load_config
    from .serving.services.service import StorageService

    config = load_config(args.config)
    service = StorageService(config, watch_interval=args.watch_interval)
    app = create_combined_app(service)
    uvicorn.run(app, host=args.host, port=args.port)


def _cmd_store_init(args: argparse.Namespace) -> None:
    dest = Path(args.dir) / CONFIG_FILENAME
    if dest.exists() and not args.force:
        raise SystemExit(f"{dest} は既に存在します（上書きは --force）")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_INIT_TEMPLATE, encoding="utf-8")
    print(f"生成しました: {dest}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manystore")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="統合アプリ（REST/WS＋S3 GW）を uvicorn 起動")
    p_serve.add_argument(
        "--config", required=True, help="TOML 設定ファイル（contexts / views.featured）"
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="bind ホスト（既定 127.0.0.1）")
    p_serve.add_argument("--port", type=int, default=8000, help="bind ポート（既定 8000）")
    p_serve.add_argument(
        "--watch-interval", type=float, default=1.0, help="ポーリング監視間隔・秒（既定 1.0）"
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_store = sub.add_parser("store", help="ストア構成の操作")
    store_sub = p_store.add_subparsers(dest="store_command", required=True)
    p_init = store_sub.add_parser("init", help=f"{CONFIG_FILENAME} の雛形を生成")
    p_init.add_argument("dir", nargs="?", default=".", help="生成先ディレクトリ（既定 cwd）")
    p_init.add_argument("--force", action="store_true", help="既存ファイルを上書きする")
    p_init.set_defaults(func=_cmd_store_init)

    return parser


def main(argv: list[str] | None = None) -> None:
    import sys

    args_list = list(sys.argv[1:] if argv is None else argv)
    # 後方互換: 旧 `manystore --config X`（先頭がフラグ＝サブコマンド無し）は `serve` に振る。
    if args_list and args_list[0].startswith("-"):
        args_list = ["serve", *args_list]
    args = _build_parser().parse_args(args_list)
    args.func(args)


if __name__ == "__main__":
    main()
