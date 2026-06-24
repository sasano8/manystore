"""backends — backend 毎の具体実装（Local / S3 / NATS / HTTP）と KVS のファクトリ。

抽象（[KeyValueStore] / [FileStore]）は `stores` に、ここには backend 実装だけを置く。
重い依存（aiobotocore / nats）は各 backend のメソッド内で遅延 import する。
"""

from pathlib import Path

from ..protocols import KeyValueStore
from .http_store import HttpFileStore, HttpKeyValueStore
from .local import LocalFileObject, LocalFileStore, LocalKeyValueStore
from .memory import DictFileStore, DictKeyValueStore
from .nats import NatsFileStore, NatsObjectKeyValueStore
from .s3 import S3FileStore, S3KeyValueStore

__all__ = [
    "DictKeyValueStore",
    "DictFileStore",
    "LocalKeyValueStore",
    "LocalFileStore",
    "LocalFileObject",
    "S3KeyValueStore",
    "S3FileStore",
    "NatsObjectKeyValueStore",
    "NatsFileStore",
    "HttpKeyValueStore",
    "HttpFileStore",
    "create_key_value_store",
]


def create_key_value_store(
    backend: str,
    local_dir: Path | None = None,
    s3_bucket: str = "",
    s3_endpoint: str = "",
    s3_region: str = "us-east-1",
    s3_access_key: str = "",
    s3_secret_key: str = "",
    s3_addressing_style: str = "virtual",
    nats_url: str = "",
    nats_bucket: str = "manystore_files",
    http_base_url: str = "",
    http_headers: dict[str, str] | None = None,
) -> KeyValueStore:
    if backend == "memory":
        return DictKeyValueStore()  # プロセス内 dict（揮発・接続不要）
    elif backend == "local":
        if local_dir is None:
            raise ValueError("local backend requires local_dir")
        return LocalKeyValueStore(local_dir)
    elif backend == "s3":
        return S3KeyValueStore(
            bucket=s3_bucket,
            endpoint_url=s3_endpoint,
            region=s3_region,
            access_key=s3_access_key,
            secret_key=s3_secret_key,
            addressing_style=s3_addressing_style,
        )
    elif backend == "nats":
        return NatsObjectKeyValueStore(url=nats_url, bucket=nats_bucket)
    elif backend == "http":
        return HttpKeyValueStore(base_url=http_base_url, headers=http_headers)
    else:
        raise ValueError(f"unknown backend: {backend!r}")
