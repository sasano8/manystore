"""http_client — manystore.server の REST protocol を喋るクライアント。

[StorageClient] は薄い SDK。[RemoteKeyValueStore] は 1 context を [KeyValueStore] 準拠の
ストアとして被せ、サーバ越しに put/get/list/exists/delete/cp/mv を行う（read-only の
backends/http_store の RW 版に相当）。httpx を遅延 import する。
"""

from collections.abc import AsyncIterator
from urllib.parse import quote

from ..async_storage import FileInfo, _kv_copy, _kv_move
from ..implement.protocol import ContextInfo, EntryInfo


def _quote_key(key: str) -> str:
    # key 内の '/' は階層として残し、その他の予約文字だけエスケープする。
    return quote(key, safe="/")


class StorageClient:
    """manystore.server に対する薄い HTTP SDK（サーバ横断）。"""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        transport: object | None = None,
    ) -> None:
        import httpx

        # transport は in-process な ASGITransport を差し込むためのテスト用フック（実運用は None）。
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, transport=transport
        )

    async def list_contexts(self) -> list[ContextInfo]:
        r = await self._client.get("/contexts")
        r.raise_for_status()
        return [
            ContextInfo(name=c["name"], backend=c["backend"], writable=c.get("writable", True))
            for c in r.json()["contexts"]
        ]

    async def list_entries(
        self, context: str, prefix: str = "", limit: int = 1000
    ) -> list[EntryInfo]:
        r = await self._client.get(
            f"/contexts/{context}/keys", params={"prefix": prefix, "limit": limit}
        )
        r.raise_for_status()
        return [EntryInfo(key=e["key"], size=e["size"]) for e in r.json()["entries"]]

    async def get(self, context: str, key: str) -> bytes | None:
        r = await self._client.get(f"/contexts/{context}/objects/{_quote_key(key)}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    async def exists(self, context: str, key: str) -> bool:
        r = await self._client.head(f"/contexts/{context}/objects/{_quote_key(key)}")
        return r.status_code == 200

    async def put(self, context: str, key: str, value: bytes) -> None:
        r = await self._client.put(f"/contexts/{context}/objects/{_quote_key(key)}", content=value)
        r.raise_for_status()

    async def delete(self, context: str, key: str) -> None:
        r = await self._client.delete(f"/contexts/{context}/objects/{_quote_key(key)}")
        r.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


class RemoteKeyValueStore:
    """1 つの context をサーバ越しに [KeyValueStore] として扱うストア（RW）。"""

    def __init__(
        self,
        base_url: str,
        context: str,
        *,
        headers: dict[str, str] | None = None,
        transport: object | None = None,
    ) -> None:
        self._client = StorageClient(base_url, headers=headers, transport=transport)
        self._context = context

    async def put(self, key: str, value: bytes) -> None:
        await self._client.put(self._context, key, value)

    async def get(self, key: str) -> bytes | None:
        return await self._client.get(self._context, key)

    async def iter(self) -> AsyncIterator[FileInfo]:
        for e in await self._client.list_entries(self._context, limit=10_000):
            yield FileInfo(filename=e.key, size=e.size)

    async def list(self, limit: int = 10) -> list[FileInfo]:
        entries = await self._client.list_entries(self._context, limit=limit)
        return [FileInfo(filename=e.key, size=e.size) for e in entries]

    async def exists(self, key: str) -> bool:
        return await self._client.exists(self._context, key)

    async def delete(self, key: str) -> None:
        await self._client.delete(self._context, key)

    async def cp(self, src: str, dst: str) -> None:
        await _kv_copy(self, src, dst)

    async def mv(self, src: str, dst: str) -> None:
        await _kv_move(self, src, dst)

    async def connect(self) -> None:
        return None

    async def aclose(self) -> None:
        await self._client.aclose()
