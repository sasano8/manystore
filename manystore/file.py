"""manystore.file — ファイルストレージ群（open_reader/open_writer のストリーム指向）。

`FileStore` 抽象（バイナリ専用。`open_reader` / `open_writer`）と各 backend・KVS アダプタ・
安全ラッパをここに集約して公開する（値ストアの [manystore.kv] と名前空間で分離）。
トップ `manystore` からも再エクスポートする。
"""

from .async_storage import FileInfo, FileObject, FileStore, KeyValueFileStore
from .backends import (
    HttpFileStore,
    LocalFileObject,
    LocalFileStore,
    NatsFileStore,
    S3FileStore,
)
from .safe_path import SafeFileStore, UnsafePathError, validate_safe_path
from .sync_storage import SyncFileObject, SyncFileStore

__all__ = [
    # shared
    "FileInfo",
    # abstraction
    "FileStore",
    "FileObject",
    # backends
    "LocalFileStore",
    "LocalFileObject",
    "S3FileStore",
    "NatsFileStore",
    "HttpFileStore",
    # KVS → FileStore アダプタ
    "KeyValueFileStore",
    # sync
    "SyncFileStore",
    "SyncFileObject",
    # safe path
    "SafeFileStore",
    "validate_safe_path",
    "UnsafePathError",
]
