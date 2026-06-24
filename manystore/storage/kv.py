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
from ..connect import ConnectPolicy, connect_key_value_store, connecting
from ..protocols import (
    AsyncKeyValueStore,
    FileInfo,
    KeyValueFromFileStore,
    KeyValueStoreBase,
    SupportsPrefixListing,
    SyncKeyValueStore,
    iter_prefix,
    scan_prefix,
)
from .surfaces.array import DEFAULT_CACHE_DIR, ArrayKeyValueStore, DownloadCache
from .surfaces.safe import SafeKeyValueStore, UnsafePathError, validate_safe_path
from .surfaces.sync_bridge import AsyncToSyncKeyValueStore

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
    # safe factory（ライブラリの顔＝Safe 包装込みの入口）
    "open_async_key_value_store",
    # safe path
    "SafeKeyValueStore",
    "validate_safe_path",
    "UnsafePathError",
]


def open_async_key_value_store(
    backend: str,
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
    **opts: object,
):
    """安全な KeyValueStore を開く入口（ライブラリの顔）＝[SafeKeyValueStore] 包装込みの接続 CM。

    `async with open_async_key_value_store("local", local_dir=...) as store:` の形で使う。
    キー検証付きの [SafeKeyValueStore] を connect して yield・終了時 aclose する。
    **Safe 包装は必須**（生 backend を直接触らせない＝パストラバーサル等を防ぐ）。生が要るときだけ
    低レベルの [create_key_value_store] / [connect_key_value_store] を使う。
    """
    return connecting(
        lambda: SafeKeyValueStore(create_key_value_store(backend, **opts)),
        verify=verify,
        policy=policy,
    )
