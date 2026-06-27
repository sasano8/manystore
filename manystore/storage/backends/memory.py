"""memory backend — プロセス内の辞書（dict）ストア（KVS / FileStore）。

依存ゼロ・接続不要のインメモリ実装。テストの参照 backend や、軽量な一時ストアに使う
（プロセス終了で揮発）。dict は **kv 寄り**（whole get/put が native）なので、核は KVS 側に置き、
FileStore は KVS を継承して IO を buffer 合成する（systemPatterns 原則7）。
"""

from collections.abc import AsyncIterator

from ...exceptions import NotFoundError
from ...protocols import (
    AsyncFileObject,
    FileInfo,
    KeyValueStoreBase,
    _kv_copy,
    _kv_move,
    _KvReadFileObject,
    _KvWriteFileObject,
)


class DictKeyValueStore(KeyValueStoreBase):
    """`dict[str, bytes]` を保持するインメモリ [KeyValueStore]。

    外から既存 dict を渡せば共有・観測できる（テストの fake 兼参照実装）。iter は他 backend と
    同じく名前降順。get の primitive は get_or_raise（欠損で FileNotFoundError）で、get(default) は
    基底 [KeyValueStoreBase] が与える。
    """

    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self._data: dict[str, bytes] = data if data is not None else {}

    async def put(self, key: str, value: bytes) -> FileInfo:
        self._data[key] = bytes(value)
        return {"filename": key, "size": len(value)}

    async def get_or_raise(self, key: str) -> bytes:
        try:
            return self._data[key]
        except KeyError as e:
            raise NotFoundError(key) from e  # 欠損は NotFoundError に正規化

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # 名前降順（他 backend と整合）。dict にサーバ側 prefix は無い＝scan+filter で支える。
        # prefix で絞ってから limit を適用する（limit は「絞り込み後」の件数上限）。
        count = 0
        for key in sorted(self._data, reverse=True):
            if prefix and not key.startswith(prefix):
                continue
            if limit is not None and count >= limit:
                return
            yield FileInfo(filename=key, size=len(self._data[key]))
            count += 1

    async def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]:
        return [info async for info in self.iter_all(limit, prefix)]

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
