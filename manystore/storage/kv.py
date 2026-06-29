"""manystore.kv — キーバリューストア群（put/get がメインの値ストア）。

`KeyValueStore` 抽象と各 backend・合成・接続・安全ラッパをここに集約して公開する
（ファイル指向の [manystore.file] と名前空間で分離）。トップ `manystore` からも再エクスポートする。
"""

from collections.abc import Mapping
from contextlib import asynccontextmanager

from ..protocols import (
    AsyncKeyValueStore,
    FileInfo,
    IfMatch,
    KeyValueFromFileStore,
    KeyValueStoreBase,
    SyncKeyValueStore,
    Verify,
)
from .backends import (
    DictKeyValueStore,
    HttpKeyValueStore,
    LocalKeyValueStore,
    NatsObjectKeyValueStore,
    S3KeyValueStore,
    create_unsafe_key_value_store,
)
from .connect import ConnectPolicy, connect_key_value_store, connecting
from .surfaces.array import DEFAULT_CACHE_DIR, ArrayKeyValueStore, DownloadCache
from .surfaces.safe import SafeKeyValueStore, UnsafePathError, validate_safe_path
from .surfaces.sync_bridge import AsyncToSyncKeyValueStore
from .sync import StorageMirror, SyncPlan, SyncResult

__all__ = [
    # shared
    "FileInfo",
    # conditional put（CAS）: if_match の型（不在は FileInfo.absent() で作る）
    "IfMatch",
    # abstraction
    "AsyncKeyValueStore",
    "KeyValueStoreBase",
    # backends
    "DictKeyValueStore",
    "LocalKeyValueStore",
    "S3KeyValueStore",
    "NatsObjectKeyValueStore",
    "HttpKeyValueStore",
    # 低レベル factory（生＝未接続・キー検証なし）。生口はトップ公開に残す（名前で unsafe 明示）。
    "create_unsafe_key_value_store",
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
    # download の整合性検証ポリシー（ビットフラグ）
    "Verify",
    # 2 ストア片方向同期（one-way mirror）
    "StorageMirror",
    "SyncPlan",
    "SyncResult",
    # connection lifecycle
    "ConnectPolicy",
    "connecting",
    "connect_key_value_store",
    # safe factory（Safe 包装込み・未接続）＋ 顔（Safe 包装＋接続 CM）
    "create_safe_key_value_store",
    "create_safe_array_store",
    "open_async_key_value_store",
    "open_async_array_store",
    # safe path
    "SafeKeyValueStore",
    "validate_safe_path",
    "UnsafePathError",
]


def create_safe_key_value_store(backend: str, **opts: object) -> SafeKeyValueStore:
    """安全な（キー検証付き）[KeyValueStore] を**構築のみ**で返す（未接続）。

    生 backend（[create_unsafe_key_value_store]）を [SafeKeyValueStore] で 1 枚包む。接続は呼び出し
    側（`async with connecting(...)` 等）に委ねる＝接続まで一括で欲しいなら顔
    [open_async_key_value_store] を使う。
    """
    return SafeKeyValueStore(create_unsafe_key_value_store(backend, **opts))


async def create_safe_array_store(mounts: Mapping[str, AsyncKeyValueStore]) -> SafeKeyValueStore:
    """安全な合成ストアを**構築のみ**で返す（未接続）。async＝`mount` が非同期 IF のため。

    `mounts`（論理名 → backend）を [ArrayKeyValueStore] に**登録**し（mount は I/O なし）、
    [SafeKeyValueStore] で 1 枚包む（合成キー `<mount>/<subkey>` を検証）。接続は呼び出し側に委ねる
    ＝接続まで一括で欲しいなら顔 [open_async_array_store] を使う。
    """
    arr = ArrayKeyValueStore()
    for name, store in mounts.items():
        await arr.mount(name, store)  # 登録のみ（I/O なし。mount は非同期 IF）
    return SafeKeyValueStore(arr)  # connect/aclose は下層 array へ委譲＝全 mount を一括


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
    **Safe 包装は必須**（生 backend を直接触らせない＝パストラバーサル等を防ぐ）。Safe だけ欲しく
    接続は自前なら [create_safe_key_value_store]、生が要るなら [create_unsafe_key_value_store] /
    [connect_key_value_store]。
    """
    return connecting(
        lambda: create_safe_key_value_store(backend, **opts),
        verify=verify,
        policy=policy,
    )


@asynccontextmanager
async def open_async_array_store(
    mounts: Mapping[str, AsyncKeyValueStore],
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
):
    """安全な合成ストアを開く入口（ライブラリの顔）＝[ArrayKeyValueStore] を [SafeKeyValueStore] で
    包んだ接続 CM。

    `async with open_async_array_store({"docs": store_a, "imgs": store_b}) as arr:` の形で使う。
    `mounts`（論理名 → backend）を [create_safe_array_store] で構築し（mount は I/O なし）、CM 突入
    時に全 mount を connect・終了時に aclose する。**接続ライフサイクルはこの CM が一括で担う**
    （mount は登録のみで connect しない＝二重責務を解消）。キー検証は合成キー。
    """
    # 登録は事前に済ませ（I/O なし）、接続ライフサイクルは connecting に委ねる。
    safe = await create_safe_array_store(mounts)
    async with connecting(lambda: safe, verify=verify, policy=policy) as store:
        yield store
