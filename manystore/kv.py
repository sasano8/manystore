"""manystore.kv — キーバリューストア群（put/get がメインの値ストア）。

`KeyValueStore` 抽象と各 backend・合成・接続・安全ラッパをここに集約して公開する
（ファイル指向の [manystore.file] と名前空間で分離）。トップ `manystore` からも再エクスポートする。
"""

from .backends import (
    DictKeyValueStore,
    HttpKeyValueStore,
    LocalKeyValueStore,
    NatsObjectKeyValueStore,
    S3KeyValueStore,
    create_key_value_store,
)
from .connect import ConnectPolicy, connect_key_value_store, connecting
from .protocols import (
    AsyncKeyValueStore,
    FileInfo,
    KeyValueFromFileStore,
    KeyValueStoreBase,
    SupportsPrefixListing,
    SyncKeyValueStore,
    iter_prefix,
    scan_prefix,
)
from .stores.array import DEFAULT_CACHE_DIR, ArrayKeyValueStore, DownloadCache
from .stores.safe import SafeKeyValueStore, UnsafePathError, validate_safe_path
from .stores.sync_bridge import AsyncToSyncKeyValueStore

__all__ = [
    # shared
    "FileInfo",
    # abstraction
    "AsyncKeyValueStore",
    "KeyValueStoreBase",
    # optional capability（prefix 列挙）
    "SupportsPrefixListing",
    "iter_prefix",
    "scan_prefix",
    # backends
    "DictKeyValueStore",
    "LocalKeyValueStore",
    "S3KeyValueStore",
    "NatsObjectKeyValueStore",
    "HttpKeyValueStore",
    "create_key_value_store",
    # FileStore → KVS アダプタ（KeyValueFileStore の逆向き）
    "KeyValueFromFileStore",
    # sync / bridge
    "SyncKeyValueStore",
    "AsyncKeyValueStore",
    "AsyncToSyncKeyValueStore",
    # composite
    "ArrayKeyValueStore",
    "DownloadCache",
    "DEFAULT_CACHE_DIR",
    # connection lifecycle
    "ConnectPolicy",
    "connecting",
    "connect_key_value_store",
    # safe path
    "SafeKeyValueStore",
    "validate_safe_path",
    "UnsafePathError",
]
