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
)
# from .stores.base import FileInfo, FileObject, FileStore, KeyValueFileStore
from .stores.safe import SafeFileStore, UnsafePathError, validate_safe_path
from .protocols import SyncFileObject, SyncFileStore, FileInfo, FileObject, SyncKeyValueStore, FileStore, KeyValueStore

# TODO: 名前の整理
KeyValueFileStore = SyncKeyValueStore
AsyncKeyValueStore = KeyValueStore
AsyncFileStore = FileStore

__all__ = [
    # shared
    "FileInfo",
    # abstraction
    "FileStore",
    "FileObject",
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
    # safe path
    "SafeFileStore",
    "validate_safe_path",
    "UnsafePathError",
]
