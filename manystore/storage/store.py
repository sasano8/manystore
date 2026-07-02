"""manystore.store — 統合ストア facade（M071）＝put/get も open_* も持つ**1 つの Store** の入口。

M071 で kv/file の二本立て（旧 `manystore.kv` / `manystore.file`）を 1 つに畳んだ**唯一の正面口**。
トップ `manystore` からも全てフラット再エクスポートする。

- 型: [AsyncStore]（唯一の公開ストア型）／`AsyncBufferedStore`（put/get だけの view）。
- 入口: [open_store]（URL/名前）・[open_async_store]（backend 名＋接続 CM）・[create_safe_store]
  （Safe 包装）・[create_unsafe_store]（生）。合成ストアは [open_async_array_store]。
- backend: `DictStore`/`LocalStore`/`S3Store`/`NatsStore`/`HttpStore`/`RemoteStore`（1=1 Store）。
- registry/plugin: [register_backend]・[BackendSpec]・[get_backend_spec]・[list_backends]。
"""

from collections.abc import Mapping
from contextlib import asynccontextmanager

from ..client import RemoteStore
from ..spec import AsyncBufferedStore, AsyncStore, FileInfo, IfMatch, SyncStore, Verify
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
from .connect import ConnectPolicy, connect_store, connecting
from .surfaces.array import DEFAULT_CACHE_DIR, ArrayStore, DownloadCache
from .surfaces.safe import SafeStore, UnsafePathError, validate_safe_path
from .surfaces.sync_bridge import AsyncToSyncStore
from .sync import StorageMirror, SyncPlan, SyncResult
from .url import parse_store_url

# ── 統合ストアの入口（M071・kv/file の顔を 1 本に）──


def create_safe_store(backend: str, **opts: object) -> SafeStore:
    """安全な（検証付き）**full Store**（put/get＋open_*）を**構築のみ**返す（未接続・M071）。

    生 [create_unsafe_store] を [SafeStore] で 1 枚包む。接続まで一括で欲しいなら
    顔 [open_async_store]。
    """
    return SafeStore(create_unsafe_store(backend, **opts))


def open_async_store(
    backend: str,
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
    **opts: object,
):
    """安全な full Store を開く入口（ライブラリの顔）＝[SafeStore] 包装込みの接続 CM（M071）。

    `async with open_async_store("local", local_dir=...) as store:` の形で使う。put/get も
    open_reader/open_writer も同じ 1 つのストアで扱える。URL/構成名から開くなら [open_store]。
    **Safe 包装は必須**（生 backend を直接触らせない＝パストラバーサル等を防ぐ）。Safe だけ欲しく
    接続は自前なら [create_safe_store]、生が要るなら [create_unsafe_store]。
    """
    return connecting(
        lambda: create_safe_store(backend, **opts),
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
    """名前 URL または構成ファイルの context 名から安全な full Store を開く（fsspec 風）。

    `async with open_store("s3://bkt?endpoint=http://h:9000") as store:`（URL・M069）／
    `async with open_store("mycontext") as store:`（構成ファイルの context 名・M070）の形で使う。

    - `target` に `://` があれば **URL**＝[parse_store_url] で分解（`docs/url_scheme.md`）。
    - 無ければ **context 名**＝`manystore.toml` を上方向 discovery（`config` で明示も可）して解決。
      空文字は `default_context`。local 相対パスは**構成ファイルのディレクトリ基準**で解決される。
    いずれも顔 [open_async_store] へ委譲＝Safe 包装＋接続 CM の full Store（M071）。
    """
    if "://" in target:
        backend, opts = parse_store_url(target)
    else:
        backend, opts = _resolve_context(target, config)
    return open_async_store(backend, verify=verify, policy=policy, **opts)


# ── 合成ストア（array）の入口 ──


async def create_safe_array_store(mounts: Mapping[str, AsyncBufferedStore]) -> SafeStore:
    """安全な合成ストアを**構築のみ**で返す（未接続）。async＝`mount` が非同期 IF のため。

    `mounts`（論理名 → backend）を [ArrayStore] に**登録**し（mount は I/O なし）、
    [SafeStore] で 1 枚包む（合成キー `<mount>/<subkey>` を検証）。接続は呼び出し側に委ねる
    ＝接続まで一括で欲しいなら顔 [open_async_array_store] を使う。
    """
    arr = ArrayStore()
    for name, store in mounts.items():
        await arr.mount(name, store)  # 登録のみ（I/O なし。mount は非同期 IF）
    return SafeStore(arr)  # connect/aclose は下層 array へ委譲＝全 mount を一括


@asynccontextmanager
async def open_async_array_store(
    mounts: Mapping[str, AsyncBufferedStore],
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
):
    """安全な合成ストアを開く入口（ライブラリの顔）＝[ArrayStore] を [SafeStore] で
    包んだ接続 CM。

    `async with open_async_array_store({"docs": store_a, "imgs": store_b}) as arr:` の形で使う。
    `mounts`（論理名 → backend）を [create_safe_array_store] で構築し（mount は I/O なし）、CM 突入
    時に全 mount を connect・終了時に aclose する。**接続ライフサイクルはこの CM が一括で担う**。
    """
    safe = await create_safe_array_store(mounts)
    async with connecting(lambda: safe, verify=verify, policy=policy) as store:
        yield store


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
    # 合成ストア（array）
    "ArrayStore",
    "create_safe_array_store",
    "open_async_array_store",
    "DownloadCache",
    "DEFAULT_CACHE_DIR",
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
    # 接続 / sync bridge / mirror
    "ConnectPolicy",
    "connecting",
    "connect_store",
    "AsyncToSyncStore",
    "StorageMirror",
    "SyncPlan",
    "SyncResult",
    # safe path
    "SafeStore",
    "validate_safe_path",
    "UnsafePathError",
]
