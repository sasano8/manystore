"""backends — backend 毎の具体実装（Local / S3 / NATS / HTTP / manystore）と KVS のファクトリ。

抽象は上位に、ここには backend 実装と**レジストリへの seed** を置く。「名前 → ストア生成」の解決は
[registry]（builtin/entry-point/programmatic の 3 経路）へ集約し、`create_unsafe_*_store` はその薄い
ラッパ（後方互換の入口）に留める。詳細は `docs/backend_registry.md`。重い依存（aiobotocore / nats /
httpx / client）は factory 内で遅延 import。
"""

from ...spec import AsyncBufferedStore, AsyncStreamingStore
from ..registry import (
    BackendSpec,
    get_backend_spec,
    list_backends,
    register_backend,
    register_builtin_backend,
)
from .http_store import HttpFileStore, HttpKeyValueStore, HttpStore
from .local import (
    LocalFileObject,
    LocalFileStore,
    LocalKeyValueStore,
    LocalStore,
    PosixLocalStore,
    WindowsLocalStore,
)
from .memory import DictFileStore, DictKeyValueStore, DictStore
from .nats import NatsFileStore, NatsObjectKeyValueStore, NatsStore
from .s3 import S3FileStore, S3KeyValueStore, S3Store

__all__ = [
    # 1 backend = 1 Store（M071・full Store）
    "DictStore",
    "LocalStore",
    "PosixLocalStore",  # local の OS 別実装（M079）
    "WindowsLocalStore",  # 未実装スタブ（M079）
    "S3Store",
    "NatsStore",
    "HttpStore",
    "LocalFileObject",
    # 旧名 alias（非推奨・M071）
    "DictKeyValueStore",
    "DictFileStore",
    "LocalKeyValueStore",
    "LocalFileStore",
    "S3KeyValueStore",
    "S3FileStore",
    "NatsObjectKeyValueStore",
    "NatsFileStore",
    "HttpKeyValueStore",
    "HttpFileStore",
    "BackendSpec",
    "register_backend",
    "get_backend_spec",
    "list_backends",
    "create_unsafe_store",
    "create_unsafe_key_value_store",
    "create_unsafe_file_store",
]


# ── builtin backend の単一 factory（未接続の full Store を作る・M071。opts は flat kwargs＝M069）──


def _make_memory(**opts: object) -> AsyncStreamingStore:
    return DictStore()  # プロセス内 dict（揮発・接続不要）


def _make_local(**opts: object) -> AsyncStreamingStore:
    local_dir = opts.get("local_dir")
    if local_dir is None:
        raise ValueError("local backend requires local_dir")
    return LocalStore(local_dir)  # type: ignore[arg-type]


def _make_s3(**opts: object) -> AsyncStreamingStore:
    return S3Store(
        bucket=opts.get("s3_bucket", ""),  # type: ignore[arg-type]
        endpoint_url=opts.get("s3_endpoint", ""),  # type: ignore[arg-type]
        region=opts.get("s3_region", "us-east-1"),  # type: ignore[arg-type]
        access_key=opts.get("s3_access_key", ""),  # type: ignore[arg-type]
        secret_key=opts.get("s3_secret_key", ""),  # type: ignore[arg-type]
        addressing_style=opts.get("s3_addressing_style", "virtual"),  # type: ignore[arg-type]
    )


def _make_nats(**opts: object) -> AsyncStreamingStore:
    return NatsStore(
        url=opts.get("nats_url", ""),  # type: ignore[arg-type]
        bucket=opts.get("nats_bucket", "manystore_files"),  # type: ignore[arg-type]
    )


def _make_http(**opts: object) -> AsyncStreamingStore:
    return HttpStore(
        base_url=opts.get("http_base_url", ""),  # type: ignore[arg-type]
        headers=opts.get("http_headers"),  # type: ignore[arg-type]
    )


def _make_manystore(**opts: object) -> AsyncStreamingStore:
    # manystore 自身の HTTP サービスを喋る client（`client/` 在中）を遅延 import。opts は暫定。
    from ...client.remote import RemoteStore

    return RemoteStore(
        base_url=opts.get("base_url", ""),  # type: ignore[arg-type]
        context=opts.get("context", ""),  # type: ignore[arg-type]
        headers=opts.get("headers"),  # type: ignore[arg-type]
    )


# ── builtin を予約名として seed（import 時に一度）＝1 backend=1 factory（M071）──
register_builtin_backend("memory", factory=_make_memory)
register_builtin_backend("local", factory=_make_local)
register_builtin_backend("s3", factory=_make_s3)
register_builtin_backend("nats", factory=_make_nats)
register_builtin_backend("http", factory=_make_http)
register_builtin_backend("manystore", factory=_make_manystore)


def create_unsafe_store(backend: str, **opts: object) -> AsyncStreamingStore:
    """生の（未接続・検証なし）**full Store**を作る＝[registry] 単一 factory の薄いラッパ（M071）。

    **unsafe**＝`../escape` 等を弾かない（対策は呼び出し側）。安全に使うなら [create_safe_store]／顔
    [open_async_store]。opts は backend 固有（例: `local_dir=`/`s3_bucket=`/`http_base_url=`）。
    """
    return get_backend_spec(backend).factory(**opts)


def create_unsafe_key_value_store(backend: str, **opts: object) -> AsyncBufferedStore:
    """**非推奨（M071）＝[create_unsafe_store] へ統合**（full Store を返す）。"""
    return create_unsafe_store(backend, **opts)


def create_unsafe_file_store(backend: str, **opts: object) -> AsyncStreamingStore:
    """**非推奨（M071）＝[create_unsafe_store] へ統合**（backend は 1 クラスで常に full Store）。"""
    return create_unsafe_store(backend, **opts)
