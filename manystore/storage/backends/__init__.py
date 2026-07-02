"""backends — backend 毎の具体実装（Local / S3 / NATS / HTTP / manystore）と KVS のファクトリ。

抽象は上位に、ここには backend 実装と**レジストリへの seed** を置く。「名前 → ストア生成」の解決は
[registry]（builtin/entry-point/programmatic の 3 経路）へ集約し、`create_unsafe_*_store` はその薄い
ラッパ（後方互換の入口）に留める。詳細は `docs/backend_registry.md`。重い依存（aiobotocore / nats /
httpx / client）は factory 内で遅延 import。
"""

from ...protocols import AsyncBufferedStore, AsyncStreamingStore
from .http_store import HttpFileStore, HttpKeyValueStore
from .local import LocalFileObject, LocalFileStore, LocalKeyValueStore
from .memory import DictFileStore, DictKeyValueStore
from .nats import NatsFileStore, NatsObjectKeyValueStore
from .registry import (
    BackendSpec,
    get_backend_spec,
    list_backends,
    register_backend,
    register_builtin_backend,
)
from .s3 import S3FileStore, S3KeyValueStore

__all__ = [
    "DictKeyValueStore",
    "DictFileStore",
    "LocalKeyValueStore",
    "LocalFileStore",
    "LocalFileObject",
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
    "create_unsafe_key_value_store",
    "create_unsafe_file_store",
]


# ── builtin backend の factory（未接続のストアを作る。opts は暫定の flat kwargs＝M069 で整理）──


def _kv_memory(**opts: object) -> AsyncBufferedStore:
    return DictKeyValueStore()  # プロセス内 dict（揮発・接続不要）


def _file_memory(**opts: object) -> AsyncStreamingStore:
    return DictFileStore()


def _kv_local(**opts: object) -> AsyncBufferedStore:
    local_dir = opts.get("local_dir")
    if local_dir is None:
        raise ValueError("local backend requires local_dir")
    return LocalKeyValueStore(local_dir)  # type: ignore[arg-type]


def _file_local(**opts: object) -> AsyncStreamingStore:
    local_dir = opts.get("local_dir")
    if local_dir is None:
        raise ValueError("local backend requires local_dir")
    return LocalFileStore(local_dir)  # type: ignore[arg-type]


def _s3_kwargs(opts: dict[str, object]) -> dict[str, object]:
    return dict(
        bucket=opts.get("s3_bucket", ""),
        endpoint_url=opts.get("s3_endpoint", ""),
        region=opts.get("s3_region", "us-east-1"),
        access_key=opts.get("s3_access_key", ""),
        secret_key=opts.get("s3_secret_key", ""),
        addressing_style=opts.get("s3_addressing_style", "virtual"),
    )


def _kv_s3(**opts: object) -> AsyncBufferedStore:
    return S3KeyValueStore(**_s3_kwargs(opts))  # type: ignore[arg-type]


def _file_s3(**opts: object) -> AsyncStreamingStore:
    return S3FileStore(**_s3_kwargs(opts))  # type: ignore[arg-type]


def _kv_nats(**opts: object) -> AsyncBufferedStore:
    return NatsObjectKeyValueStore(
        url=opts.get("nats_url", ""),  # type: ignore[arg-type]
        bucket=opts.get("nats_bucket", "manystore_files"),  # type: ignore[arg-type]
    )


def _file_nats(**opts: object) -> AsyncStreamingStore:
    return NatsFileStore(
        url=opts.get("nats_url", ""),  # type: ignore[arg-type]
        bucket=opts.get("nats_bucket", "manystore_files"),  # type: ignore[arg-type]
    )


def _kv_http(**opts: object) -> AsyncBufferedStore:
    return HttpKeyValueStore(
        base_url=opts.get("http_base_url", ""),  # type: ignore[arg-type]
        headers=opts.get("http_headers"),  # type: ignore[arg-type]
    )


def _file_http(**opts: object) -> AsyncStreamingStore:
    return HttpFileStore(
        base_url=opts.get("http_base_url", ""),  # type: ignore[arg-type]
        headers=opts.get("http_headers"),  # type: ignore[arg-type]
    )


def _kv_manystore(**opts: object) -> AsyncBufferedStore:
    # manystore 自身の HTTP サービスを喋る client（`client/` 在中）を遅延 import。opts は暫定。
    from ...client.remote import RemoteKeyValueStore

    return RemoteKeyValueStore(
        base_url=opts.get("base_url", ""),  # type: ignore[arg-type]
        context=opts.get("context", ""),  # type: ignore[arg-type]
        headers=opts.get("headers"),  # type: ignore[arg-type]
    )


# ── builtin を予約名として seed（import 時に一度）──
register_builtin_backend("memory", kv_factory=_kv_memory, file_factory=_file_memory)
register_builtin_backend("local", kv_factory=_kv_local, file_factory=_file_local)
register_builtin_backend("s3", kv_factory=_kv_s3, file_factory=_file_s3)
register_builtin_backend("nats", kv_factory=_kv_nats, file_factory=_file_nats)
register_builtin_backend("http", kv_factory=_kv_http, file_factory=_file_http)
# manystore は remote client を KVS として。FileStore は非対応（file_factory=None）。
register_builtin_backend("manystore", kv_factory=_kv_manystore, file_factory=None)


def create_unsafe_key_value_store(backend: str, **opts: object) -> AsyncBufferedStore:
    """backend 名から生の（未接続・**キー検証なし**）[KeyValueStore] を作る低レベルファクトリ。

    [registry] の薄いラッパ。**unsafe**＝`../escape` 等を弾かない（対策は呼び出し側責務）。安全に
    使うなら [create_safe_key_value_store]（Safe 包装）か顔の `open_async_key_value_store`
    （Safe＋接続）。
    opts は backend 固有（例: `local_dir=` / `s3_bucket=` / `http_base_url=`）。
    """
    return get_backend_spec(backend).kv_factory(**opts)


def create_unsafe_file_store(backend: str, **opts: object) -> AsyncStreamingStore:
    """[create_unsafe_key_value_store] の FileStore 版（backend → 完全な [FileStore]＝KVS + IO）。

    http は read-only（書き込み・一覧は `io.UnsupportedOperation`）。FileStore 非対応の
    backend（例 `manystore`）は [ValueError]。opts は KVS 版と同形。
    """
    spec = get_backend_spec(backend)
    if spec.file_factory is None:
        raise ValueError(f"backend {backend!r} does not provide a FileStore")
    return spec.file_factory(**opts)
