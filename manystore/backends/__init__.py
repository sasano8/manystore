"""backends — backend 毎の具体実装（Local / S3 / NATS）と KVS のファクトリ。

抽象（[KeyValueStore] / [FileStore]）は [async_storage] に、ここには実装だけを置く。
重い依存（aiobotocore / nats）は各 backend のメソッド内で遅延 import する。
"""

from pathlib import Path

from ..async_storage import KeyValueStore
from .local import LocalFileObject, LocalFileStore, LocalKeyValueStore
from .nats import NatsFileStore, NatsObjectKeyValueStore
from .s3 import S3FileStore, S3KeyValueStore

__all__ = [
    "LocalKeyValueStore",
    "LocalFileStore",
    "LocalFileObject",
    "S3KeyValueStore",
    "S3FileStore",
    "NatsObjectKeyValueStore",
    "NatsFileStore",
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
) -> KeyValueStore:
    if backend == "local":
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
    else:
        raise ValueError(f"unknown backend: {backend!r}")
