"""manystore.kv — キーバリューストア群（put/get がメインの値ストア）。

`KeyValueStore` 抽象と各 backend・合成・接続・安全ラッパをここに集約して公開する
（ファイル指向の [manystore.file] と名前空間で分離）。トップ `manystore` からも再エクスポートする。
"""

from collections.abc import Mapping
from contextlib import asynccontextmanager

from ..protocols import (
    AsyncBufferedStore,
    BufferedStoreBase,
    FileInfo,
    IfMatch,
    KeyValueFromFileStore,
    SyncBufferedStore,
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
from .config import (
    ContextConfig,
    StoreConfig,
    discover_store_config,
    find_config_file,
    load_store_config,
)
from .connect import ConnectPolicy, connect_key_value_store, connecting
from .surfaces.array import DEFAULT_CACHE_DIR, ArrayKeyValueStore, DownloadCache
from .surfaces.safe import SafeKeyValueStore, UnsafePathError, validate_safe_path
from .surfaces.sync_bridge import AsyncToSyncKeyValueStore
from .sync import StorageMirror, SyncPlan, SyncResult
from .url import parse_store_url

__all__ = [
    # shared
    "FileInfo",
    # conditional put（CAS）: if_match の型（不在は FileInfo.absent() で作る）
    "IfMatch",
    # abstraction
    "AsyncBufferedStore",
    "BufferedStoreBase",
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
    "SyncBufferedStore",
    "AsyncBufferedStore",
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
    # URL でストアを開く（fsspec 風・M069）
    "open_store",
    "parse_store_url",
    # 構成ファイルからストア復元（M070）
    "StoreConfig",
    "ContextConfig",
    "load_store_config",
    "discover_store_config",
    "find_config_file",
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


async def create_safe_array_store(mounts: Mapping[str, AsyncBufferedStore]) -> SafeKeyValueStore:
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


def _resolve_context(name: str, config: StoreConfig | None) -> tuple[str, dict[str, object]]:
    """context 名を構成ファイルから `(backend, opts)` へ解決する（M070）。"""
    cfg = config if config is not None else discover_store_config()
    if cfg is None:
        raise ValueError(
            f"構成ファイル（manystore.toml）が見つからない＝context {name!r} を解決できない"
            "（`manystore store init` で作成／URL 形式 'scheme://…' で直接指定）"
        )
    ctx_name = name or cfg.default_context
    if not ctx_name:
        raise ValueError("context 名が空で default_context も未設定＝解決できない")
    ctx = cfg.contexts.get(ctx_name)
    if ctx is None:
        known = ", ".join(sorted(cfg.contexts)) or "(none)"
        raise ValueError(f"unknown context: {ctx_name!r}（既知: {known}）")
    return ctx.backend, ctx.opts


def open_store(
    target: str,
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
    config: StoreConfig | None = None,
):
    """名前 URL または構成ファイルの context 名から安全な KeyValueStore を開く（fsspec 風）。

    `async with open_store("s3://bkt?endpoint=http://h:9000") as store:`（URL・M069）／
    `async with open_store("mycontext") as store:`（構成ファイルの context 名・M070）の形で使う。

    - `target` に `://` があれば **URL**＝[parse_store_url] で分解（`docs/url_scheme.md`）。
    - 無ければ **context 名**＝`manystore.toml` を上方向 discovery（`config` で明示も可）して解決。
      空文字は `default_context`。local 相対パスは**構成ファイルのディレクトリ基準**で解決される。
    いずれも顔 [open_async_key_value_store] へ委譲＝Safe 包装＋接続 CM。
    """
    from .file import open_async_store  # full Store の顔（M071）。kv→file の一方向 import

    if "://" in target:
        backend, opts = parse_store_url(target)
    else:
        backend, opts = _resolve_context(target, config)
    # full Store（put/get＋open_*）を返す＝URL/名前でも顔と同じ統合ストア（M071）。
    return open_async_store(backend, verify=verify, policy=policy, **opts)


@asynccontextmanager
async def open_async_array_store(
    mounts: Mapping[str, AsyncBufferedStore],
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
