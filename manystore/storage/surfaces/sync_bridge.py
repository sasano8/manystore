"""async-to-sync storage — 非同期ストアを同期として被せるブリッジ。

[AsyncToSyncKeyValueStore] は非同期 [KeyValueStore] を同期 [SyncBufferedStore] として被せる。
ストレージの一次実装は async に保ち、同期版はこのブリッジだけで得る
（手書きの二重実装を避ける）。専属のイベントループを 1 つ保持し、各呼び出しを
`run_until_complete` で同期化する（接続を保持する nats 等のため、呼び出し毎にループを作らず
使い回す）。実行中のイベントループからは呼べない（同期 CLI 等、ループ外の同期コードから使う前提）。

注: FileStore（open でファイルオブジェクト）の async→sync ブリッジは、ファイルオブジェクト境界の
扱いが別途必要なため未実装（必要になってから）。
"""

import asyncio
from collections.abc import Iterator

from ...protocols import AsyncBufferedStore, FileInfo, IfMatch


class AsyncToSyncKeyValueStore:
    """非同期 [KeyValueStore] を [SyncBufferedStore] として同期 API で被せるラッパ。"""

    def __init__(self, store: AsyncBufferedStore) -> None:
        self._store = store
        self._loop = asyncio.new_event_loop()

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        return self._run(self._store.put(key, value, if_match=if_match))

    def create(self, key: str, value: bytes) -> FileInfo:
        return self._run(self._store.create(key, value))

    def head(self, key: str) -> FileInfo:
        return self._run(self._store.head(key))

    def head_or_absent(self, key: str) -> FileInfo:
        return self._run(self._store.head_or_absent(key))

    def get_or_raise(self, key: str) -> bytes:
        return self._run(self._store.get_or_raise(key))

    def get(self, key: str, default: bytes | None = None) -> bytes | None:
        return self._run(self._store.get(key, default))

    def iter_all(self, limit: int | None = None, prefix: str = "") -> Iterator[FileInfo]:
        # async イテレータを 1 回のループ実行で全件取得してから同期的に流す。
        # __anext__ を毎回駆動する方式は async ジェネレータの finalize が厄介なため、
        # comprehension で取り切る（async ジェネレータはこの実行内で確実に閉じられる）。
        async def _collect() -> list[FileInfo]:
            return [info async for info in self._store.iter_all(limit, prefix)]

        yield from self._run(_collect())

    def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]:
        # list は async 側の実装を 1 回のループ実行で取り切る（item 毎の駆動を避ける）。
        return self._run(self._store.list_all(limit, prefix))

    def exists(self, key: str) -> bool:
        return self._run(self._store.exists(key))

    def delete(self, key: str) -> None:
        self._run(self._store.delete(key))

    def cp(self, src: str, dst: str) -> None:
        self._run(self._store.cp(src, dst))

    def mv(self, src: str, dst: str) -> None:
        self._run(self._store.mv(src, dst))

    def connect(self) -> None:
        self._run(self._store.connect())

    def close(self) -> None:
        """ラップ先を aclose し、保持しているループを閉じる（async ジェネレータを finalize）。"""
        try:
            self._run(self._store.aclose())
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            finally:
                self._loop.close()

    def __enter__(self) -> AsyncToSyncKeyValueStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
