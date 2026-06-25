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

from ...protocols import AsyncFileObject, FileInfo, KeyValueStoreBase, _KvReadFileObject


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


class HttpKeyValueStore(KeyValueStoreBase, _HttpBase):
    """HTTP 越しの read-only KVS。`get` / `exists` のみ実装し、書き込み・一覧は非対応。"""

    async def put(self, key: str, value: bytes) -> None:
        _read_only("put")

    async def get_or_raise(self, key: str) -> bytes:
        async with self._client() as client:
            resp = await client.get(self._url(key))
            if resp.status_code == 404:
                raise FileNotFoundError(key)  # 欠損は FileNotFoundError に正規化
            resp.raise_for_status()
            return resp.content

    async def iter_all(self, limit: int | None = None) -> AsyncIterator[FileInfo]:
        raise io.UnsupportedOperation("http backend is read-only: list/iter")
        yield  # 未到達（この関数を async generator にするため）

    async def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        # read-only＝列挙不可。明示的に非対応を上げる（暗黙 scan に落とさない）。
        raise io.UnsupportedOperation("http backend is read-only: iter_prefix")
        yield  # 未到達（async generator 化のため）

    async def list_all(self, limit: int | None = None) -> list[FileInfo]:
        _read_only("list_all")

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


class HttpFileStore(HttpKeyValueStore):
    """HTTP 越しの read-only な完全 [FileStore]（= [HttpKeyValueStore] ＋ read IO）。

    HTTP は **kv 寄り**（GET=whole get）かつ read-only。KVS 面（get/get_or_raise/exists・
    write 系は `io.UnsupportedOperation`）は HttpKeyValueStore から継承する。open_reader は
    **whole get の上に buffer 合成**（[_KvReadFileObject] で get_or_raise を再利用）。
    open_writer は非対応。真の streaming が要るなら将来 httpx の `client.stream` で逐次化できる。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return _KvReadFileObject(await self.get_or_raise(filename))  # 欠損は FileNotFoundError

    async def open_writer(self, filename: str) -> AsyncFileObject:
        _read_only("open_writer")
