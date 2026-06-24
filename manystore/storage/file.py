"""manystore.file — ファイルストレージ群（open_reader/open_writer のストリーム指向）。

`FileStore` 抽象（バイナリ専用。`open_reader` / `open_writer`）と各 backend・KVS アダプタ・
安全ラッパをここに集約して公開する（値ストアの [manystore.kv] と名前空間で分離）。
トップ `manystore` からも再エクスポートする。
"""

from .backends import (
    DictFileStore,
    HttpFileStore,
    LocalFileObject,
    LocalFileStore,
    NatsFileStore,
    S3FileStore,
    create_file_store,
)
from ..connect import ConnectPolicy, connecting
from ..protocols import (
    AsyncFileObject,
    AsyncFileStore,
    AsyncKeyValueStore,
    FileInfo,
    KeyValueFileStore,
    SyncFileObject,
    SyncFileStore,
    SyncKeyValueStore,
)
from .surfaces.safe import SafeFileStore, UnsafePathError, validate_safe_path

__all__ = [
    # shared
    "FileInfo",
    # abstraction
    "AsyncFileStore",
    "AsyncFileObject",
    # backends
    "DictFileStore",
    "LocalFileStore",
    "LocalFileObject",
    "S3FileStore",
    "NatsFileStore",
    "HttpFileStore",
    # KVS → FileStore アダプタ
    "KeyValueFileStore",
    "SyncKeyValueStore",
    "AsyncKeyValueStore",
    # sync
    "SyncFileStore",
    "SyncFileObject",
    "AsyncFileStore",
    # safe factory（ライブラリの顔＝Safe 包装込みの入口）
    "create_file_store",
    "open_async_file_store",
    # safe path
    "SafeFileStore",
    "validate_safe_path",
    "UnsafePathError",
]


def open_async_file_store(
    backend: str,
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
    **opts: object,
):
    """安全な FileStore を開く入口（ライブラリの顔）＝[SafeFileStore] 包装込みの接続 CM。

    `async with open_async_file_store("local", local_dir=...) as fs:` の形で使う。
    検証付きの完全な [SafeFileStore]（KVS + IO）を connect して yield・終了時 aclose。
    **Safe 包装は必須**（生 backend を直接触らせない）。生が要るときだけ [create_file_store]。
    """
    return connecting(
        lambda: SafeFileStore(create_file_store(backend, **opts)),
        verify=verify,
        policy=policy,
    )
