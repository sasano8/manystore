"""client 層のテスト（in-process ASGITransport で server と往復）。

RemoteKeyValueStore が [KeyValueStore] 準拠でサーバ越しに put/get/list/exists/delete/cp/mv できる。
pytest-asyncio（asyncio_mode=auto）で `async def test_*` をそのまま回す。
"""

from pathlib import Path

import httpx

from manystore.client import RemoteKeyValueStore
from manystore.implement.config import parse_config
from manystore.implement.service import StorageService
from manystore.server.app import create_app


async def test_remote_kvs_roundtrip(tmp_path: Path) -> None:
    cfg = parse_config(
        {"contexts": {"work": {"backend": "local", "root": str(tmp_path)}}},
    )
    service = StorageService(cfg, watch_interval=1.0)

    # lifespan を介さず手動接続（ASGITransport は lifespan を起動しないため）。
    await service.connect()
    app = create_app(service)
    transport = httpx.ASGITransport(app=app)
    store = RemoteKeyValueStore("http://test", "work", transport=transport)
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
