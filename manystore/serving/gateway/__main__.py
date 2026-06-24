"""`python -m manystore.gateway --config <toml>` で uvicorn 起動する CLI。

manystore を S3 互換 API として公開する。bucket = config の context。既定 bind は
localhost（127.0.0.1）＝SigV4 署名検証はせず gateway 自身の認証層（既定 localhost）に
委譲するため、既定では自ホストに閉じる（外部公開は明示的に `--host 0.0.0.0`）。
"""

import argparse

from ..services.config import load_config
from ..services.service import StorageService
from .app import create_gateway


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="manystore.gateway")
    parser.add_argument(
        "--config", required=True, help="TOML 設定ファイル（contexts＝S3 バケットに公開）"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind ホスト（既定 127.0.0.1）")
    parser.add_argument("--port", type=int, default=9000, help="bind ポート（既定 9000）")
    parser.add_argument(
        "--watch-interval", type=float, default=1.0, help="ポーリング監視間隔・秒（既定 1.0）"
    )
    args = parser.parse_args(argv)

    import uvicorn

    config = load_config(args.config)
    service = StorageService(config, watch_interval=args.watch_interval)
    app = create_gateway(service)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
