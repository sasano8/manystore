"""safe path — ストアのキー/パスに不正（パストラバーサル等）が無いか検証するラッパ。

[validate_safe_path] が POSIX 相対パスのみを許し、絶対パス・`..`・バックスラッシュ・NUL を弾く。
[SafeKeyValueStore] / [SafeFileStore] は同じインターフェイスを被せ、キー/filename を検証してから
委譲する（path を覗いて検証するため各メソッドを明示的に書く＝型情報もそのまま引き継がれる）。
"""

from collections.abc import AsyncIterator

from .async_storage import FileInfo, FileObject, FileStore, KeyValueStore, KeyValueStoreBase
from .exceptions import UnsafePathError  # 集約先（後方互換: ここからも import できる）

__all__ = ["UnsafePathError", "validate_safe_path", "SafeKeyValueStore", "SafeFileStore"]


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


class SafeKeyValueStore(KeyValueStoreBase):
    """キーを [validate_safe_path] で検証してから委譲する [KeyValueStore] ラッパ。"""

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    async def put(self, key: str, value: bytes) -> None:
        await self._store.put(validate_safe_path(key), value)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(validate_safe_path(key))

    def iter_all(self) -> AsyncIterator[FileInfo]:
        return self._store.iter_all()

    async def list_all(self, limit: int = 10) -> list[FileInfo]:
        return await self._store.list_all(limit)

    async def exists(self, key: str) -> bool:
        return await self._store.exists(validate_safe_path(key))

    async def delete(self, key: str) -> None:
        await self._store.delete(validate_safe_path(key))

    async def cp(self, src: str, dst: str) -> None:
        await self._store.cp(validate_safe_path(src), validate_safe_path(dst))

    async def mv(self, src: str, dst: str) -> None:
        await self._store.mv(validate_safe_path(src), validate_safe_path(dst))

    async def connect(self) -> None:
        await self._store.connect()

    async def aclose(self) -> None:
        await self._store.aclose()


class SafeFileStore:
    """filename を [validate_safe_path] で検証してから委譲する [FileStore] ラッパ。"""

    def __init__(self, store: FileStore) -> None:
        self._store = store

    async def open_reader(self, filename: str) -> FileObject:
        return await self._store.open_reader(validate_safe_path(filename))

    async def open_writer(self, filename: str) -> FileObject:
        return await self._store.open_writer(validate_safe_path(filename))
