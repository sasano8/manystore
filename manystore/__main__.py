"""`manystore` / `python -m manystore` の CLI（Typer）。

- `manystore serve --config <toml>` … 統合アプリ（native REST/WS ＋ S3 互換 GW）を起動（M023）。
  native REST/WS（`/kv/raw/...`＝buffered）と S3 互換ゲートウェイ（`/storage/s3/...`＝streaming）を
  1 プロセス・1 共有 [StorageService] で公開する。既定 bind は localhost（外部公開は `--host`）。
- `manystore store init [dir]` … ストア構成ファイル `manystore.toml` の雛形を生成（M070）。

後方互換: 旧 `python -m manystore --config <toml>`（サブコマンド無し）は `serve` に振る。
単体起動（`python -m manystore.serving.server` / `python -m manystore.serving.gateway`）も利用可能。
重い依存（uvicorn/fastapi）は `serve` 実行時に遅延 import する。
"""

import sys
from pathlib import Path
from typing import Annotated

import typer

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

app = typer.Typer(help="manystore CLI", no_args_is_help=True, add_completion=False)
store_app = typer.Typer(help="ストア構成の操作", no_args_is_help=True)
app.add_typer(store_app, name="store")


@app.command()
def serve(
    config: Annotated[str, typer.Option(help="TOML 設定ファイル（contexts / views.featured）")],
    host: Annotated[str, typer.Option(help="bind ホスト")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="bind ポート")] = 8000,
    watch_interval: Annotated[float, typer.Option(help="ポーリング監視間隔・秒")] = 1.0,
) -> None:
    """統合アプリ（REST/WS＋S3 GW）を uvicorn 起動する。"""
    import uvicorn

    from .serving.combined import create_combined_app
    from .serving.services.config import load_config
    from .serving.services.service import StorageService

    service = StorageService(load_config(config), watch_interval=watch_interval)
    uvicorn.run(create_combined_app(service), host=host, port=port)


@store_app.command("init")
def store_init(
    dir: Annotated[str, typer.Argument(help="生成先ディレクトリ")] = ".",
    force: Annotated[bool, typer.Option(help="既存ファイルを上書きする")] = False,
) -> None:
    """`manystore.toml` の雛形を生成する。"""
    dest = Path(dir) / CONFIG_FILENAME
    if dest.exists() and not force:
        typer.echo(f"{dest} は既に存在します（上書きは --force）", err=True)
        raise typer.Exit(code=1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_INIT_TEMPLATE, encoding="utf-8")
    typer.echo(f"生成しました: {dest}")


def main(argv: list[str] | None = None) -> None:
    """CLI エントリ（console script / `python -m manystore`）。

    後方互換: 旧 `manystore --config X`（先頭が `--config`＝サブコマンド無し）は `serve` に振る
    （`--help`/`--version` 等は素通し＝トップレベルのサブコマンド一覧を出す）。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0].startswith("--config"):
        args = ["serve", *args]
    # Typer(click) は standalone で成功時も SystemExit(0) を投げる。0/None だけ飲み込み（テスト等の
    # 呼び出しで例外にしない）、非ゼロ（エラー）は伝播する（プロセス終了コードは保つ）。
    try:
        app(args=args, prog_name="manystore")
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


if __name__ == "__main__":
    main()
