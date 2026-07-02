"""manystore.file — ファイルストレージ群（open_reader/open_writer のストリーム指向）。

`FileStore` 抽象（バイナリ専用。`open_reader` / `open_writer`）と各 backend・KVS アダプタ・
安全ラッパをここに集約して公開する（値ストアの [manystore.kv] と名前空間で分離）。
トップ `manystore` からも再エクスポートする。
"""

from ..protocols import (
    AsyncBufferedStore,
    AsyncFileObject,
    AsyncStreamingStore,
    FileInfo,
    KeyValueFileStore,
    SyncBufferedStore,
    SyncFileObject,
    SyncStreamingStore,
)
from .backends import (
    DictFileStore,
    HttpFileStore,
    LocalFileObject,
    LocalFileStore,
    NatsFileStore,
    S3FileStore,
    create_unsafe_file_store,
)
from .connect import ConnectPolicy, connecting
from .surfaces.safe import SafeFileStore, UnsafePathError, validate_safe_path

__all__ = [
    # shared
    "FileInfo",
    # abstraction
    "AsyncStreamingStore",
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
    "SyncBufferedStore",
    "AsyncBufferedStore",
    # sync
    "SyncStreamingStore",
    "SyncFileObject",
    "AsyncStreamingStore",
    # 低レベル factory（生＝未接続・キー検証なし）。生口はトップ公開に残す（名前で unsafe 明示）。
    "create_unsafe_file_store",
    # safe factory（Safe 包装込み・未接続）＋ 顔（Safe 包装＋接続 CM）
    "create_safe_file_store",
    "open_async_file_store",
    # safe path
    "SafeFileStore",
    "validate_safe_path",
    "UnsafePathError",
]


def create_safe_file_store(backend: str, **opts: object) -> SafeFileStore:
    """安全な（filename/キー検証付き）完全な [FileStore]（KVS + IO）を**構築のみ**で返す（未接続）。

    生 backend（[create_unsafe_file_store]）を [SafeFileStore] で 1 枚包む。接続は呼び出し側に委ねる
    ＝接続まで一括で欲しいなら顔 [open_async_file_store] を使う。
    """
    return SafeFileStore(create_unsafe_file_store(backend, **opts))


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
    **Safe 包装は必須**（生 backend を直接触らせない）。Safe だけ欲しく接続は自前なら
    [create_safe_file_store]、生が要るなら [create_unsafe_file_store]。
    """
    return connecting(
        lambda: create_safe_file_store(backend, **opts),
        verify=verify,
        policy=policy,
    )
