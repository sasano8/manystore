"""manystore.file — ファイルストレージ群（open_reader/open_writer のストリーム指向）。

`FileStore` 抽象（バイナリ専用。`open_reader` / `open_writer`）と各 backend・KVS アダプタ・
安全ラッパをここに集約して公開する（値ストアの [manystore.kv] と名前空間で分離）。
トップ `manystore` からも再エクスポートする。
"""

from .stores.base import FileInfo, FileObject, FileStore, KeyValueFileStore
from .backends import (
    DictFileStore,
    HttpFileStore,
    LocalFileObject,
    LocalFileStore,
    NatsFileStore,
    S3FileStore,
)
from .stores.safe import SafeFileStore, UnsafePathError, validate_safe_path
from .sync_storage import SyncFileObject, SyncFileStore

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
    # sync
    "SyncFileStore",
    "SyncFileObject",
    # safe path
    "SafeFileStore",
    "validate_safe_path",
    "UnsafePathError",
]
