"""nats backend — NATS JetStream Object Store（KVS / FileStore）。

nats-py はメソッド内で遅延 import する。FileStore は read=全体取得 / write=close で put。
"""

import contextlib
from collections.abc import AsyncIterator

from ..async_storage import (
    FileInfo,
    FileObject,
    KeyValueStoreBase,
    _kv_copy,
    _kv_move,
    _KvReadFileObject,
    _KvWriteFileObject,
    _take,
    scan_prefix,
)


class _NatsBase:
    """NATS object store の共通接続部（lazy connect の `_get_obs`）。"""

    def __init__(self, url: str, bucket: str) -> None:
        self._url = url
        self._bucket = bucket
        self._nc = None
        self._obs = None

    async def _get_obs(self):
        if self._obs is None:
            import nats
            from nats.js.errors import BucketNotFoundError

            self._nc = await nats.connect(self._url)
            js = self._nc.jetstream()
            try:
                self._obs = await js.object_store(self._bucket)
            except BucketNotFoundError:
                self._obs = await js.create_object_store(self._bucket)
        return self._obs

    async def connect(self) -> None:
        # nc 接続＋object store を確立する（以降は使い回す）。
        await self._get_obs()

    async def aclose(self) -> None:
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
            self._obs = None


class NatsObjectKeyValueStore(KeyValueStoreBase, _NatsBase):
    async def put(self, key: str, value: bytes) -> None:
        obs = await self._get_obs()
        await obs.put(key, value)

    async def get_or_raise(self, key: str) -> bytes:
        obs = await self._get_obs()
        try:
            result = await obs.get(key)
        except Exception as e:
            raise FileNotFoundError(key) from e  # 欠損は FileNotFoundError に正規化
        return result.data or b""

    async def iter_all(self) -> AsyncIterator[FileInfo]:
        obs = await self._get_obs()
        try:
            entries = await obs.list()
        except Exception:
            entries = []
        entries = [e for e in entries if not e.deleted]
        entries.sort(key=lambda e: e.name, reverse=True)
        for e in entries:
            yield FileInfo(filename=e.name, size=e.size or 0)

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        # NATS にサーバ側 prefix は無い＝scan で明示的に支える（暗黙 fallback ではない）。
        return scan_prefix(self, prefix)

    async def list_all(self, limit: int = 10) -> list[FileInfo]:
        return await _take(self.iter_all(), limit)

    async def exists(self, key: str) -> bool:
        obs = await self._get_obs()
        try:
            info = await obs.get_info(key)  # ObjectStore に info は無い。get_info が正
            return not info.deleted
        except Exception:
            return False

    async def delete(self, key: str) -> None:
        obs = await self._get_obs()
        with contextlib.suppress(Exception):
            await obs.delete(key)

    async def cp(self, src: str, dst: str) -> None:
        await _kv_copy(self, src, dst)

    async def mv(self, src: str, dst: str) -> None:
        await _kv_move(self, src, dst)


# ── FileStore（= KVS ＋ buffer 合成 IO） ──


class NatsFileStore(NatsObjectKeyValueStore):
    """NATS の完全な [FileStore]（= [NatsObjectKeyValueStore] ＋ buffer 合成 IO）。

    NATS Object Store は **kv 寄り**＝whole get/put が native（核は KVS 側）。真の bounded
    ストリーミングは `get(writeinto=...)` の逐次配送が nats-py 仕様で executor スレッドから呼ばれ、
    スレッド安全な受け渡しが要るため未採用＝deferred。よって open_reader/open_writer は **whole
    get/put の上に buffer で被せた擬似ストリーム**（共有の [_KvReadFileObject]/[_KvWriteFileObject]
    を流用）。KVS 面は継承。
    """

    async def open_reader(self, filename: str) -> FileObject:
        return _KvReadFileObject(await self.get_or_raise(filename))  # whole get を buffer 化

    async def open_writer(self, filename: str) -> FileObject:
        return _KvWriteFileObject(self, filename)  # close で whole put
