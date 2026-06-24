"""`python -m manystore --config <toml>` で統合アプリを uvicorn 起動する CLI（M023）。

manystore ネイティブ REST/WS（`/kv/raw/...`＝buffered）と S3 互換ゲートウェイ
（`/storage/s3/...`＝streaming）を 1 つのプロセス・1 つの共有 [StorageService] で公開する
（名前空間は M025 で buffer 性ごとに再編）。S3 クライアントは
`endpoint_url=<host>/storage/s3` を向ける（path-style）。

既定 bind は localhost（127.0.0.1）。S3 側は SigV4 を検証せず gateway 認証へ委ねるため、
外部公開は明示的に `--host 0.0.0.0` を要求する（フル CRUD / S3 を晒すため自ホストに閉じる）。

単体起動（`python -m manystore.server` / `python -m manystore.gateway`）は従来どおり利用可能。
"""

import argparse

from .combined import create_combined_app
from .serving.services.config import load_config
from .serving.services.service import StorageService


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="manystore")
    parser.add_argument(
        "--config",
        required=True,
        help="TOML 設定ファイル（contexts＝REST/S3 で公開・views.featured）",
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
    app = create_combined_app(service)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
