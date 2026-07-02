"""manystore.store — 統合ストア facade（M071）＝put/get も open_* も持つ**1 つの Store** の入口。

M071 で kv/file の二本立て（`manystore.kv` / `manystore.file`）を 1 つに畳んだ正面口。旧 2 facade は
非推奨 alias として残す（当面 re-export）。トップ `manystore` からも全てフラット再エクスポートする。

- 型: [AsyncStore]（唯一の公開ストア型）／`AsyncBufferedStore`（put/get だけの view）。
- 入口: [open_store]（URL/名前）・[open_async_store]（backend 名＋接続 CM）・[create_safe_store]
  （Safe 包装）・[create_unsafe_store]（生）。
- backend: `DictStore`/`LocalStore`/`S3Store`/`NatsStore`/`HttpStore`/`RemoteStore`（1=1 Store）。
- registry/plugin: [register_backend]・[BackendSpec]・[get_backend_spec]・[list_backends]。
"""

# manystore backend の remote client を store 面から辿れるように再輸出。
from ..client import RemoteStore  # noqa: E402  （client は storage の外＝末尾で import）
from ..protocols import AsyncBufferedStore, AsyncStore, FileInfo, IfMatch, SyncStore, Verify
from .backends import (
    BackendSpec,
    DictStore,
    HttpStore,
    LocalStore,
    NatsStore,
    S3Store,
    create_unsafe_store,
    get_backend_spec,
    list_backends,
    register_backend,
)
from .config import (
    ContextConfig,
    StoreConfig,
    discover_store_config,
    find_config_file,
    load_store_config,
)
from .connect import ConnectPolicy, connecting
from .file import SafeStore, create_safe_store, open_async_store
from .kv import open_store, parse_store_url
from .surfaces.safe import UnsafePathError, validate_safe_path

__all__ = [
    # 公開型（唯一の Store・view）
    "AsyncStore",
    "SyncStore",
    "AsyncBufferedStore",
    # shared 型
    "FileInfo",
    "IfMatch",
    "Verify",
    # 入口（統合）
    "open_store",
    "open_async_store",
    "create_safe_store",
    "create_unsafe_store",
    "parse_store_url",
    # backend（1 backend=1 Store）
    "DictStore",
    "LocalStore",
    "S3Store",
    "NatsStore",
    "HttpStore",
    "RemoteStore",
    # registry / plugin
    "register_backend",
    "BackendSpec",
    "get_backend_spec",
    "list_backends",
    # 構成ファイル
    "StoreConfig",
    "ContextConfig",
    "load_store_config",
    "discover_store_config",
    "find_config_file",
    # 接続 / safe path
    "ConnectPolicy",
    "connecting",
    "SafeStore",
    "validate_safe_path",
    "UnsafePathError",
]
