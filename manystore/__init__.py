"""manystore — 差し替え可能なバックエンドを持つストア群。

2 種のストア抽象を、async / sync / async-to-sync の 3 モジュールに分けて持つ
（将来 juice の外のライブラリとして抽出する想定）:
- [KeyValueStore] … put/get がメインの値ストア（Local / S3 / NATS バックエンド同梱）。
- [FileStore]     … `open` でファイルオブジェクト（[FileObject]）を取得するストリーム指向の抽象。

モジュール:
- [async_storage]         … ストア抽象（[KeyValueStore] / [FileStore]）＋共通ヘルパ＋汎用アダプタ。
- [backends]              … backend 毎の具体実装（Local / S3 / NATS）とファクトリ。
- [sync_storage]          … 同期インターフェイス（[SyncKeyValueStore] / [SyncFileStore]）。
- [async_to_sync_storage] … 非同期を同期として被せるブリッジ（[AsyncToSyncKeyValueStore]）。

公開 API はここで再エクスポートする。`__init__` 直下は stdlib のみに依存し、重い backend
（redis / nats / aiobotocore / httpx）は各 backend のメソッド内で遅延 import する。
"""

from .array_storage import DEFAULT_CACHE_DIR, ArrayKeyValueStore, DownloadCache
from .async_storage import (
    FileInfo,
    FileObject,
    FileStore,
    KeyValueFileStore,
    KeyValueStore,
)
from .async_to_sync_storage import AsyncToSyncKeyValueStore
from .backends import (
    LocalFileObject,
    LocalFileStore,
    LocalKeyValueStore,
    NatsFileStore,
    NatsObjectKeyValueStore,
    S3FileStore,
    S3KeyValueStore,
    create_key_value_store,
)
from .connect import ConnectPolicy, connect_key_value_store, connecting
from .safe_path import (
    SafeFileStore,
    SafeKeyValueStore,
    UnsafePathError,
    validate_safe_path,
)
from .sync_storage import SyncFileObject, SyncFileStore, SyncKeyValueStore

__all__ = [
    # shared
    "FileInfo",
    # key-value store (put/get)
    "KeyValueStore",
    "LocalKeyValueStore",
    "S3KeyValueStore",
    "NatsObjectKeyValueStore",
    "create_key_value_store",
    "SyncKeyValueStore",
    "AsyncToSyncKeyValueStore",
    "ArrayKeyValueStore",
    # connection lifecycle
    "ConnectPolicy",
    "connecting",
    "connect_key_value_store",
    # safe path (validation wrapper)
    "validate_safe_path",
    "UnsafePathError",
    "SafeKeyValueStore",
    "SafeFileStore",
    # download cache (ArrayStorage wrapper)
    "DownloadCache",
    "DEFAULT_CACHE_DIR",
    # file store (open -> file object)
    "FileStore",
    "FileObject",
    "LocalFileStore",
    "LocalFileObject",
    "KeyValueFileStore",
    "S3FileStore",
    "NatsFileStore",
    "SyncFileStore",
    "SyncFileObject",
]
