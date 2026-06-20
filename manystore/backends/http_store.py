"""http backend — HTTP/HTTPS 越しの read-only ストア。

KVS の get/exists と FileStore の read のみ実装する。httpx はメソッド内で遅延 import する。
書き込み系（put/delete/cp/mv）と一覧（list/iter）は read-only ゆえ非対応
（`io.UnsupportedOperation`）。キーは `base_url` への相対パスとして URL を組み立てる
（`base_url + "/" + key`）。認証等が要るときは `headers` を渡す。

モジュール名は標準ライブラリの `http` パッケージと紛れないよう `http_store` にしている
（backend 識別子は `"http"` のまま）。
"""

import io
from collections.abc import AsyncIterator

from ..async_storage import FileInfo, FileObject, _KvReadFileObject


def _read_only(op: str) -> None:
    raise io.UnsupportedOperation(f"http backend is read-only: {op}")


class _HttpBase:
    """HTTP 系ストアの共通部（base_url / headers / timeout と client 生成）。"""

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = headers or {}
        self._timeout = timeout

    def _url(self, key: str) -> str:
        return f"{self._base_url}/{key.lstrip('/')}"

    def _client(self):
        import httpx

        return httpx.AsyncClient(
            headers=self._headers, timeout=self._timeout, follow_redirects=True
        )

    async def connect(self) -> None:
        # 到達確認はしない（エンドポイントごとに HEAD/GET の挙動が違うため）。
        return None

    async def aclose(self) -> None:
        return None


class HttpKeyValueStore(_HttpBase):
    """HTTP 越しの read-only KVS。`get` / `exists` のみ実装し、書き込み・一覧は非対応。"""

    async def put(self, key: str, value: bytes) -> None:
        _read_only("put")

    async def get(self, key: str) -> bytes | None:
        async with self._client() as client:
            resp = await client.get(self._url(key))
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content

    async def iter(self) -> AsyncIterator[FileInfo]:
        raise io.UnsupportedOperation("http backend is read-only: list/iter")
        yield  # 未到達（この関数を async generator にするため）

    async def list(self, limit: int = 10) -> list[FileInfo]:
        _read_only("list")

    async def exists(self, key: str) -> bool:
        async with self._client() as client:
            resp = await client.head(self._url(key))
            return resp.status_code < 400

    async def delete(self, key: str) -> None:
        _read_only("delete")

    async def cp(self, src: str, dst: str) -> None:
        _read_only("cp")

    async def mv(self, src: str, dst: str) -> None:
        _read_only("mv")


class HttpFileStore(_HttpBase):
    """HTTP 越しの read-only [FileStore]。read（`open(..., "rb")`）のみ。write は非対応。

    read は GET で全体を取得してバッファから返す（NATS backend と同じ方式）。真のストリーミングが
    要るなら将来 httpx の streaming（`client.stream`）で逐次化する余地がある。
    """

    async def open(self, filename: str, mode: str = "rb") -> FileObject:
        if "w" in mode:
            _read_only("open(w)")
        if "r" in mode:
            async with self._client() as client:
                resp = await client.get(self._url(filename))
                if resp.status_code == 404:
                    raise FileNotFoundError(filename)
                resp.raise_for_status()
                return _KvReadFileObject(resp.content)
        raise ValueError(f"unsupported mode for HttpFileStore: {mode!r}")
