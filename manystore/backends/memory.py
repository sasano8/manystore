"""memory backend — プロセス内の辞書（dict）ストア（KVS / FileStore）。

依存ゼロ・接続不要のインメモリ実装。テストの参照 backend や、軽量な一時ストアに使う
（プロセス終了で揮発）。dict は **kv 寄り**（whole get/put が native）なので、核は KVS 側に置き、
FileStore は KVS を継承して IO を buffer 合成する（systemPatterns 原則7）。
"""

from collections.abc import AsyncIterator

from ..protocols import (
    AsyncFileObject,
    FileInfo,
    KeyValueStoreBase,
    _kv_copy,
    _kv_move,
    _KvReadFileObject,
    _KvWriteFileObject,
    scan_prefix,
)


class DictKeyValueStore(KeyValueStoreBase):
    """`dict[str, bytes]` を保持するインメモリ [KeyValueStore]。

    外から既存 dict を渡せば共有・観測できる（テストの fake 兼参照実装）。iter は他 backend と
    同じく名前降順。get の primitive は get_or_raise（欠損で FileNotFoundError）で、get(default) は
    基底 [KeyValueStoreBase] が与える。
    """

    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self._data: dict[str, bytes] = data if data is not None else {}

    async def put(self, key: str, value: bytes) -> None:
        self._data[key] = bytes(value)

    async def get_or_raise(self, key: str) -> bytes:
        try:
            return self._data[key]
        except KeyError as e:
            raise FileNotFoundError(key) from e  # 欠損は FileNotFoundError に正規化

    async def iter_all(self, limit: int | None = None) -> AsyncIterator[FileInfo]:
        # 名前降順（他 backend と整合）。limit=None は全件（スライスがそのまま全要素）。
        for key in sorted(self._data, reverse=True)[:limit]:
            yield FileInfo(filename=key, size=len(self._data[key]))

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        return scan_prefix(self, prefix)  # dict にサーバ側 prefix は無い＝scan で明示的に支える

    async def list_all(self, limit: int | None = None) -> list[FileInfo]:
        return [info async for info in self.iter_all(limit)]

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)  # 無いキーは無視

    async def cp(self, src: str, dst: str) -> None:
        await _kv_copy(self, src, dst)

    async def mv(self, src: str, dst: str) -> None:
        await _kv_move(self, src, dst)

    async def connect(self) -> None:
        return None  # インメモリ＝接続不要

    async def aclose(self) -> None:
        return None


class DictFileStore(DictKeyValueStore):
    """`dict` を保持する完全な [FileStore]（= [DictKeyValueStore] ＋ buffer 合成 IO）。

    dict は kv 寄りなので KVS 面を継承し、open_reader/open_writer は whole get/put の上に
    buffer を被せた擬似ストリーム（共有 [_KvReadFileObject]/[_KvWriteFileObject] を流用）。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return _KvReadFileObject(await self.get_or_raise(filename))  # 欠損は FileNotFoundError

    async def open_writer(self, filename: str) -> AsyncFileObject:
        return _KvWriteFileObject(self, filename)  # close で whole put
