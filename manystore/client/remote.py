"""remote — manystore.server が公開する REST protocol を喋るクライアント。

汎用 HTTP backend（`backends/http_store.py` の単純な GET クライアント）とは別物で、
**manystore API 前提**のクライアント:
- [ManystoreClient] … manystore.server の API を呼ぶ薄い SDK（list/get/put/delete）。
- [RemoteKeyValueStore] … 1 bucket を [KeyValueStore] 準拠で被せ、サーバ越しに
  put/get/list/exists/delete/cp/mv を行う（read-only `http_store` の RW 版に相当）。

addressing は `{bucket}/{path}`（M025改）。`base_url` は native NS のルートを指す
（例 `http://host/kv/raw`）。リクエストは NS ルートからの**相対パス**で組み立てるので、
`base_url` には末尾 `/` を補って httpx の相対結合が最後のセグメントを食わないようにする。

httpx を遅延 import する。
"""

from collections.abc import AsyncIterator
from urllib.parse import quote

from ..protocols import FileInfo, KeyValueStoreBase, _kv_copy, _kv_move
from ..serving.services.protocol import ContextInfo, EntryInfo


def _quote_key(key: str) -> str:
    # key 内の '/' は階層として残し、その他の予約文字だけエスケープする。
    return quote(key, safe="/")


class ManystoreClient:
    """manystore.server の API を呼ぶ薄い SDK（サーバ横断）。"""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        transport: object | None = None,
    ) -> None:
        import httpx

        # transport は in-process な ASGITransport を差し込むためのテスト用フック（実運用は None）。
        # base_url は NS ルート。末尾 `/` を補うと相対パス結合で `{bucket}/...` がそのまま付く。
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", headers=headers, transport=transport
        )

    async def list_contexts(self) -> list[ContextInfo]:
        r = await self._client.get("")  # NS ルート＝bucket 一覧
        r.raise_for_status()
        return [
            ContextInfo(name=c["name"], backend=c["backend"], writable=c.get("writable", True))
            for c in r.json()["contexts"]
        ]

    async def list_entries(self, context: str, limit: int = 1000) -> list[EntryInfo]:
        r = await self._client.get(f"{context}/", params={"limit": limit})
        r.raise_for_status()
        return [EntryInfo(key=e["key"], size=e["size"]) for e in r.json()["entries"]]

    async def get_or_raise(self, context: str, key: str) -> bytes:
        r = await self._client.get(f"{context}/{_quote_key(key)}")
        if r.status_code == 404:
            raise FileNotFoundError(key)  # 欠損は FileNotFoundError に正規化（get_or_raise 規約）
        r.raise_for_status()
        return r.content

    async def get(self, context: str, key: str, default: bytes | None = None) -> bytes | None:
        try:
            return await self.get_or_raise(context, key)
        except FileNotFoundError:
            return default

    async def exists(self, context: str, key: str) -> bool:
        r = await self._client.head(f"{context}/{_quote_key(key)}")
        return r.status_code == 200

    async def put(self, context: str, key: str, value: bytes) -> None:
        r = await self._client.put(f"{context}/{_quote_key(key)}", content=value)
        r.raise_for_status()

    async def delete(self, context: str, key: str) -> None:
        r = await self._client.delete(f"{context}/{_quote_key(key)}")
        r.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


class RemoteKeyValueStore(KeyValueStoreBase):
    """1 つの context をサーバ越しに [KeyValueStore] として扱うストア（RW）。

    primitive `get_or_raise` だけ実装し、`get(key, default=None)` は基底 [KeyValueStoreBase]
    から受け取る（欠損は基底が捕捉して `default`）。
    """

    def __init__(
        self,
        base_url: str,
        context: str,
        *,
        headers: dict[str, str] | None = None,
        transport: object | None = None,
    ) -> None:
        self._client = ManystoreClient(base_url, headers=headers, transport=transport)
        self._context = context

    async def put(self, key: str, value: bytes) -> FileInfo:
        await self._client.put(self._context, key, value)
        return {"filename": key, "size": len(value)}

    async def get_or_raise(self, key: str) -> bytes:
        return await self._client.get_or_raise(self._context, key)

    async def iter_all(self, limit: int | None = None) -> AsyncIterator[FileInfo]:
        # HTTP 越しは真の無制限ができないので None は実上限 10_000 にクランプ（従来挙動）。
        cap = limit if limit is not None else 10_000
        for e in await self._client.list_entries(self._context, limit=cap):
            yield FileInfo(filename=e.key, size=e.size)

    async def list_all(self, limit: int | None = None) -> list[FileInfo]:
        cap = limit if limit is not None else 10_000
        entries = await self._client.list_entries(self._context, limit=cap)
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


# TODO(M042): transport 層の整理（Safepath Client / RemoteKVS の所属の切り分け）
