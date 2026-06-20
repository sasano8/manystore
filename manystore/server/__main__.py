"""`python -m manystore.server --config <toml>` で uvicorn 起動する CLI。

既定 bind は localhost（127.0.0.1）。外部公開は明示的に `--host 0.0.0.0` を要求する
（フル CRUD を晒すので、既定では自ホストに閉じる）。
"""

import argparse

from ..implement.config import load_config
from ..implement.service import StorageService
from .app import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="manystore.server")
    parser.add_argument(
        "--config", required=True, help="TOML 設定ファイル（contexts / views.featured）"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind ホスト（既定 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="bind ポート（既定 8000）")
    parser.add_argument(
        "--watch-interval", type=float, default=1.0, help="ポーリング監視間隔・秒（既定 1.0）"
    )
    args = parser.parse_args(argv)

    import uvicorn

    config = load_config(args.config)
    service = StorageService(config, watch_interval=args.watch_interval)
    app = create_app(service)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
