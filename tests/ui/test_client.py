"""client 層のテスト（in-process ASGITransport で server と往復）。

RemoteKeyValueStore が [KeyValueStore] 準拠でサーバ越しに put/get/list/exists/delete/cp/mv できる。
pytest-asyncio（asyncio_mode=auto）で `async def test_*` をそのまま回す。
"""

from pathlib import Path

import httpx
import pytest

from manystore.client import RemoteKeyValueStore
from manystore.implement.config import parse_config
from manystore.implement.service import StorageService
from manystore.server.app import create_app
from manystore.server.routes import KV_RAW_PREFIX  # native NS prefix の単一正本


async def test_remote_kvs_roundtrip(tmp_path: Path) -> None:
    cfg = parse_config(
        {"contexts": {"work": {"backend": "local", "root": str(tmp_path)}}},
    )
    service = StorageService(cfg, watch_interval=1.0)

    # lifespan を介さず手動接続（ASGITransport は lifespan を起動しないため）。
    await service.connect()
    app = create_app(service)
    transport = httpx.ASGITransport(app=app)
    # base_url = host + native NS prefix（router アタッチ先と同じ定数で組む＝ベタ書きしない）。
    store = RemoteKeyValueStore(f"http://test{KV_RAW_PREFIX}", "work", transport=transport)
    try:
        assert await store.get("a.txt") is None
        await store.put("a.txt", b"hello")
        assert await store.get("a.txt") == b"hello"
        assert await store.exists("a.txt") is True

        await store.put("b.txt", b"world")
        keys = {i["filename"] for i in await store.list_all(10)}
        assert keys == {"a.txt", "b.txt"}

        await store.cp("a.txt", "c.txt")
        assert await store.get("c.txt") == b"hello"
        await store.mv("c.txt", "d.txt")
        assert await store.get("c.txt") is None
        assert await store.get("d.txt") == b"hello"

        await store.delete("a.txt")
        assert await store.get("a.txt") is None
    finally:
        await store.aclose()
        await service.aclose()


async def test_remote_get_or_raise_and_default(tmp_path: Path) -> None:
    # get_or_raise が client/service に波及済み：欠損は FileNotFoundError、get は default を返す。
    cfg = parse_config({"contexts": {"work": {"backend": "local", "root": str(tmp_path)}}})
    service = StorageService(cfg, watch_interval=1.0)
    await service.connect()
    app = create_app(service)
    store = RemoteKeyValueStore(
        f"http://test{KV_RAW_PREFIX}", "work", transport=httpx.ASGITransport(app=app)
    )
    try:
        # サーバ層（StorageService）の get_or_raise も欠損で FileNotFoundError。
        with pytest.raises(FileNotFoundError):
            await service.get_or_raise("work", "missing.txt")

        # クライアント層（RemoteKeyValueStore）：欠損は get_or_raise が送出、get は default。
        with pytest.raises(FileNotFoundError):
            await store.get_or_raise("missing.txt")
        assert await store.get("missing.txt", default=b"fallback") == b"fallback"

        await store.put("k.txt", b"v")
        assert await store.get_or_raise("k.txt") == b"v"
    finally:
        await store.aclose()
        await service.aclose()
