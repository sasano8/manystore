"""http backend — HTTP/HTTPS 越しの read-only ストア。

KVS の get/exists と FileStore の read のみ実装する。httpx はメソッド内で遅延 import する。
書き込み系（put/delete/cp/mv）と一覧（list/iter）は read-only ゆえ非対応
（`io.UnsupportedOperation`）。キーは `base_url` への相対パスとして URL を組み立てる
（`base_url + "/" + key`）。認証等が要るときは `headers` を渡す。

モジュール名は標準ライブラリの `http` パッケージと紛れないよう `http_store` にしている
（backend 識別子は `"http"` のまま）。
"""

from collections.abc import AsyncIterator

from ...exceptions import NotFoundError, UnsupportedOperation
from ...protocols import AsyncFileObject, BufferedStoreBase, FileInfo, IfMatch


def _read_only(op: str) -> None:
    raise UnsupportedOperation(f"http backend is read-only: {op}")


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


class HttpStore(_HttpBase, BufferedStoreBase):
    """HTTP 越しの read-only な **full Store**（read のみ・M071）。

    kv 寄り＝GET が whole get。`get`/`head`/`exists` と open_reader（基底の buffer 合成）を提供し、
    書き込み・一覧・open_writer は `io.UnsupportedOperation`（read-only・open 時点で fail）。"""

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        _read_only("put")

    async def head(self, key: str) -> FileInfo:
        # read-only でも情報取得は可＝HTTP HEAD（content-length/etag を拾う）。modified_at は
        # Last-Modified の形式差を避けて None（最小）。
        async with self._client() as client:
            resp = await client.head(self._url(key))
            if resp.status_code == 404:
                raise NotFoundError(key)
            resp.raise_for_status()
            return FileInfo(
                filename=key,
                size=int(resp.headers.get("content-length", 0)),
                modified_at=None,
                etag=resp.headers.get("etag"),
            )

    async def get_or_raise(self, key: str) -> bytes:
        async with self._client() as client:
            resp = await client.get(self._url(key))
            if resp.status_code == 404:
                raise NotFoundError(key)  # 欠損は NotFoundError に正規化
            resp.raise_for_status()
            return resp.content

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # read-only＝列挙不可。明示的に非対応を上げる（暗黙 scan に落とさない）。
        raise UnsupportedOperation("http backend is read-only: list/iter")
        yield  # 未到達（この関数を async generator にするため）

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

    async def open_writer(self, filename: str) -> AsyncFileObject:
        # read-only＝open 時点で fail-fast（基底合成だと close の put まで遅延するので override）。
        _read_only("open_writer")
        # open_reader は基底 [BufferedStoreBase] の buffer 合成（whole GET）をそのまま使う。


# 旧名は alias（非推奨・M071）。
HttpKeyValueStore = HttpStore
HttpFileStore = HttpStore
