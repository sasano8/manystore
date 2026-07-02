"""safe path — ストアのキー/パスに不正（パストラバーサル等）が無いか検証するラッパ。

[validate_safe_path] が POSIX 相対パスのみを許し、絶対パス・`..`・バックスラッシュ・NUL を弾く。
[SafeStore] は full Store（put/get＋open_*）に同じインターフェイスを被せ、キー/filename を検証して
から委譲する（path を覗いて検証するため各メソッドを明示的に書く＝型情報もそのまま引き継がれる）。
"""

from collections.abc import AsyncIterator

from ...spec import (
    AsyncFileObject,
    AsyncStore,
    BufferedStoreBase,
    FileInfo,
    IfMatch,
)
from ...spec.exceptions import UnsafePathError  # 集約先（後方互換: ここからも import できる）

__all__ = ["UnsafePathError", "validate_safe_path", "SafeStore"]


def validate_safe_path(path: str) -> str:
    """`path` を検証し、安全ならそのまま返す。不正なら [UnsafePathError]。

    許可するのは POSIX 相対パスのみ。弾くもの:
    空文字 / NUL バイト / バックスラッシュ / 絶対パス（先頭 '/'） / '..' セグメント。
    """
    if not path:
        raise UnsafePathError("empty path")
    if "\x00" in path:
        raise UnsafePathError(f"NUL byte in path: {path!r}")
    if "\\" in path:
        raise UnsafePathError(f"backslash in path: {path!r}")
    if path.startswith("/"):
        raise UnsafePathError(f"absolute path: {path!r}")
    if any(seg == ".." for seg in path.split("/")):
        raise UnsafePathError(f"parent traversal in path: {path!r}")
    return path


class SafeStore(BufferedStoreBase):
    """キー/filename を [validate_safe_path] で検証してから委譲する full [Store] ラッパ。

    Store は **put/get（値 API）＋ open_reader/open_writer（IO API）を 1 つに載せた 1 IF** なので、
    KVS 面（put/get/get_or_raise・iter/exists/delete/cp/mv・connect/aclose）はキー検証込みで、
    IO 面（open_reader/open_writer）は filename 検証込みで、下層 [AsyncStore] へ委譲する。
    """

    def __init__(self, store: AsyncStore) -> None:
        self._store = store

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        return await self._store.put(validate_safe_path(key), value, if_match=if_match)

    async def head(self, key: str) -> FileInfo:
        return await self._store.head(validate_safe_path(key))

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(validate_safe_path(key))

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # prefix を validate してから下層へ素通し。空 prefix は「全件」＝検証を飛ばす
        # （validate_safe_path は空を弾くため）。
        if prefix:
            validate_safe_path(prefix)
        async for info in self._store.iter_all(limit, prefix):
            yield info

    async def exists(self, key: str) -> bool:
        return await self._store.exists(validate_safe_path(key))

    async def delete(self, key: str) -> None:
        await self._store.delete(validate_safe_path(key))

    async def cp(self, src: str, dst: str) -> None:
        await self._store.cp(validate_safe_path(src), validate_safe_path(dst))

    async def mv(self, src: str, dst: str) -> None:
        await self._store.mv(validate_safe_path(src), validate_safe_path(dst))

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return await self._store.open_reader(validate_safe_path(filename))

    async def open_writer(self, filename: str) -> AsyncFileObject:
        return await self._store.open_writer(validate_safe_path(filename))

    async def connect(self) -> None:
        await self._store.connect()

    async def aclose(self) -> None:
        await self._store.aclose()
