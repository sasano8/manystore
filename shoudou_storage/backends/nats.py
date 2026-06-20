"""nats backend — NATS JetStream Object Store（KVS / FileStore）。

nats-py はメソッド内で遅延 import する。FileStore は read=全体取得 / write=close で put。
"""

import contextlib
import io
from collections.abc import AsyncIterator

from ..async_storage import FileInfo, FileObject, _kv_copy, _kv_move, _KvReadFileObject, _take


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


class NatsObjectKeyValueStore(_NatsBase):
    async def put(self, key: str, value: bytes) -> None:
        obs = await self._get_obs()
        await obs.put(key, value)

    async def get(self, key: str) -> bytes | None:
        obs = await self._get_obs()
        try:
            result = await obs.get(key)
            return result.data
        except Exception:
            return None

    async def iter(self) -> AsyncIterator[FileInfo]:
        obs = await self._get_obs()
        try:
            entries = await obs.list()
        except Exception:
            entries = []
        entries = [e for e in entries if not e.deleted]
        entries.sort(key=lambda e: e.name, reverse=True)
        for e in entries:
            yield FileInfo(filename=e.name, size=e.size or 0)

    async def list(self, limit: int = 10) -> list[FileInfo]:
        return await _take(self.iter(), limit)

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


# ── FileStore（read=全体取得 / write=close で put） ──


class _NatsBufferedWriter:
    """書き込みをバッファし、close 時に `put` する書き込み [FileObject]。

    nats-py の put は bytes/readable を受けて wire 上でチャンク化して送る。async の write を
    そのまま流し込めないので、ここではメモリにバッファして close で一括 put する。
    """

    def __init__(self, base: _NatsBase, name: str) -> None:
        self._base = base
        self._name = name
        self._buf = io.BytesIO()
        self._closed = False

    async def read(self, size: int = -1) -> bytes:
        raise io.UnsupportedOperation("not readable")

    async def write(self, data: bytes) -> int:
        return self._buf.write(data)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        obs = await self._base._get_obs()
        await obs.put(self._name, self._buf.getvalue())
        self._buf.close()

    async def __aenter__(self) -> _NatsBufferedWriter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class NatsFileStore(_NatsBase):
    """NATS object store の [FileStore]（read=全体取得 / write=close で put）。

    read は `obs.get(name)` で全体を受け取り（nats-py 自身がチャンクを集約する）バッファから
    返す。`get(writeinto=...)` による逐次配送は nats-py が writeinto.write を executor スレッドで
    呼ぶ仕様で、asyncio.Queue 等への受け渡しがスレッド安全でないため採用しない（真の bounded
    ストリーミングはスレッド安全な受け渡しが要るので deferred）。write はバッファして close で put。
    """

    async def open(self, filename: str, mode: str = "rb") -> FileObject:
        if "r" in mode:
            obs = await self._get_obs()
            try:
                result = await obs.get(filename)
            except Exception as e:
                raise FileNotFoundError(filename) from e
            return _KvReadFileObject(result.data or b"")
        if "w" in mode:
            return _NatsBufferedWriter(self, filename)
        raise ValueError(f"unsupported mode for NatsFileStore: {mode!r}")
