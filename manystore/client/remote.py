"""remote — manystore.server が公開する REST protocol を喋るクライアント。

汎用 HTTP backend（`backends/http_store.py` の単純な GET クライアント）とは別物で、
**manystore API 前提**のクライアント:
- [ManystoreClient] … manystore.server の API を呼ぶ薄い SDK（list/get/put/delete）。
- [RemoteStore] … 1 bucket を [Store] 準拠で被せ、サーバ越しに
  put/get/list/exists/delete/cp/mv を行う（read-only `http_store` の RW 版に相当）。

addressing は `{bucket}/{path}`（M025改）。`base_url` は native NS のルートを指す
（例 `http://host/kv/raw`）。リクエストは NS ルートからの**相対パス**で組み立てるので、
`base_url` には末尾 `/` を補って httpx の相対結合が最後のセグメントを食わないようにする。

httpx を遅延 import する。
"""

from collections.abc import AsyncIterator
from urllib.parse import quote

from ..serving.services.protocol import ContextInfo, EntryInfo
from ..spec import (
    DEFAULT_LIST_LIMIT,
    MAX_HTTP_LIST_FETCH,
    BufferedStoreBase,
    FileInfo,
    IfMatch,
    _kv_copy,
    _kv_move,
)
from ..spec.exceptions import ConflictError, NotFoundError

# server 側 routes.py と対の独自メタヘッダ（size/modified_at/sha256）。ETag は標準ヘッダ。
_SIZE_HEADER = "X-Manystore-Size"
_MODIFIED_AT_HEADER = "X-Manystore-Modified-At"
_SHA256_HEADER = "X-Manystore-Sha256"


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

    async def list_entries(self, context: str, limit: int = DEFAULT_LIST_LIMIT) -> list[EntryInfo]:
        r = await self._client.get(f"{context}/", params={"limit": limit})
        r.raise_for_status()
        return [EntryInfo(key=e["key"], size=e["size"]) for e in r.json()["entries"]]

    async def get_or_raise(self, context: str, key: str) -> bytes:
        r = await self._client.get(f"{context}/{_quote_key(key)}")
        if r.status_code == 404:
            raise NotFoundError(key)  # 欠損は NotFoundError に正規化（get_or_raise 規約）
        r.raise_for_status()
        return r.content

    async def get(self, context: str, key: str, default: bytes | None = None) -> bytes | None:
        try:
            return await self.get_or_raise(context, key)
        except FileNotFoundError:
            return default

    async def exists(self, context: str, key: str) -> bool:
        r = await self._client.head(f"{context}/{_quote_key(key)}")
        if r.status_code == 404:
            return False
        # 404 以外（5xx・認証等）は「無い」に握り潰さず伝播（fail-loud＝head_meta と同規約）。
        r.raise_for_status()
        return True

    async def head_meta(self, context: str, key: str) -> dict | None:
        """HEAD でメタ（etag/size/modified_at/sha256）を読む。欠損（404）は None。"""
        r = await self._client.head(f"{context}/{_quote_key(key)}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        etag = r.headers.get("etag")
        size = r.headers.get(_SIZE_HEADER)
        modified_at = r.headers.get(_MODIFIED_AT_HEADER)
        return {
            "etag": etag.strip('"') if etag is not None else None,
            "size": int(size) if size is not None else None,
            "modified_at": float(modified_at) if modified_at is not None else None,
            "sha256": r.headers.get(_SHA256_HEADER),
        }

    async def put(
        self, context: str, key: str, value: bytes, *, headers: dict[str, str] | None = None
    ) -> None:
        r = await self._client.put(f"{context}/{_quote_key(key)}", content=value, headers=headers)
        if r.status_code == 409:
            raise ConflictError(key)  # conditional put の条件不一致（server の problem 409 を戻す）
        r.raise_for_status()

    async def delete(self, context: str, key: str) -> None:
        r = await self._client.delete(f"{context}/{_quote_key(key)}")
        r.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


class RemoteStore(BufferedStoreBase):
    """1 つの context をサーバ越しに扱う **full Store**（RW・M071）。

    kv 寄り＝put/get が native（サーバ往復）、open_reader/open_writer は基底 [BufferedStoreBase] の
    buffer 合成。primitive `get_or_raise` だけ実装し `get(default)` は基底から受け取る。
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

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        # conditional put を条件ヘッダに写す（None=LWW／不在=create-only／FileInfo=update CAS）。
        # 不一致は server problem(409)＝ManystoreClient.put が ConflictError に戻す（fail-loud）。
        headers: dict[str, str] | None = None
        if if_match is not None:
            if if_match.is_absent():
                headers = {"If-None-Match": "*"}  # create-only（不在を要求）
            else:
                headers = {"If-Match": f'"{if_match.get("etag")}"'}  # update CAS（etag 一致）
        await self._client.put(self._context, key, value, headers=headers)
        return FileInfo(filename=key, size=len(value))

    async def head(self, key: str) -> FileInfo:
        # HEAD のメタ（etag/size/modified_at）から version 付き FileInfo を組む。欠損は NotFound。
        # 既定 [BufferedStoreBase].head は get で全 body を読み etag=None＝CAS 不可ゆえ override。
        meta = await self._client.head_meta(self._context, key)
        if meta is None:
            raise NotFoundError(key)
        return FileInfo(
            filename=key,
            size=meta["size"],
            modified_at=meta["modified_at"],
            etag=meta["etag"],
            sha256=meta.get("sha256"),
        )

    async def get_or_raise(self, key: str) -> bytes:
        return await self._client.get_or_raise(self._context, key)

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # native REST API は prefix を持たない（サーバ側 prefix は S3 gateway のみ）。
        # ここは全件取得し client 側で scan+filter する（prefix 非対応 backend と同じ既定動作）。
        # HTTP 越しは無制限不可で None は実上限 MAX_HTTP_LIST_FETCH にクランプ。prefix 絞り込み時は
        # server 側 limit で取りこぼさないよう常にこの上限まで取得してから絞る。
        fetch_cap = (
            MAX_HTTP_LIST_FETCH if prefix else (limit if limit is not None else MAX_HTTP_LIST_FETCH)
        )
        count = 0
        for e in await self._client.list_entries(self._context, limit=fetch_cap):
            if prefix and not e.key.startswith(prefix):
                continue
            if limit is not None and count >= limit:
                return
            yield FileInfo(filename=e.key, size=e.size)
            count += 1

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


# TODO(M042): transport 層の整理（Safepath Client / RemoteStore の所属の切り分け）
