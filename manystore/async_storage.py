"""async storage — ストア抽象と共通ヘルパ（backend 非依存のコア）。

2 種のストア抽象を定義する:
- [KeyValueStore] … put/get がメインの値ストア（バイト列をキーで出し入れ）。
- [FileStore] … `open` でファイルオブジェクト（[FileObject]）を取得するストリーム指向の抽象。

具体的な backend（Local / S3 / NATS）の実装は [backends] サブパッケージに置く。ここには
抽象（Protocol）と、backend 横断で使う小さなヘルパ（`_take` / `_atomic_write_bytes` /
`_kv_copy` / `_kv_move`）、および任意の KVS を FileStore 化する汎用アダプタ [KeyValueFileStore]
だけを置く。
"""

import contextlib
import io
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, TypedDict


class FileInfo(TypedDict):
    filename: str
    size: int


# ── Key-Value store（put/get がメイン） ──


class KeyValueStore(Protocol):
    async def put(self, key: str, value: bytes) -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    def iter(self) -> AsyncIterator[FileInfo]: ...
    async def list(self, limit: int = 10) -> list[FileInfo]: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def cp(self, src: str, dst: str) -> None: ...
    async def mv(self, src: str, dst: str) -> None: ...
    async def connect(self) -> None: ...
    async def aclose(self) -> None: ...


async def _take(entries: AsyncIterator[FileInfo], limit: int) -> list[FileInfo]:
    """非同期イテレータから先頭 `limit` 件を集めて返す（各 backend の list 共通実装）。"""
    out: list[FileInfo] = []
    async for info in entries:
        out.append(info)
        if len(out) >= limit:
            break
    return out


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """同じディレクトリの一時ファイルへ書いてから `os.replace` で原子的に差し替える。

    途中失敗で `path` が壊れない（all-or-nothing）。一時ファイルは同一ディレクトリに作るので
    rename は同一ファイルシステム内＝アトミック。失敗時は一時ファイルを掃除する。
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


async def _kv_copy(store: KeyValueStore, src: str, dst: str) -> None:
    """get→put で src を dst へコピーする汎用実装（src が無ければ FileNotFoundError）。"""
    data = await store.get(src)
    if data is None:
        raise FileNotFoundError(src)
    await store.put(dst, data)


async def _kv_move(store: KeyValueStore, src: str, dst: str) -> None:
    """copy→delete で src を dst へ移動する汎用実装（原子的ではない）。"""
    await _kv_copy(store, src, dst)
    await store.delete(src)


# ── File store（open でファイルオブジェクトを取得） ──


class FileObject(Protocol):
    """`FileStore.open` が返すファイルオブジェクト（ストリーム）。"""

    async def read(self, size: int = -1) -> bytes: ...
    async def write(self, data: bytes) -> int: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> FileObject: ...
    async def __aexit__(self, *exc: object) -> None: ...


class FileStore(Protocol):
    """ファイルオブジェクト（[FileObject]）を取得するストリーム指向のストア（バイナリ専用）。

    `mode` 文字列を解釈する `open` ではなく、方向が型に出る 2 メソッドに分ける:
    - `open_reader(filename)` … 読み取り用（write は `io.UnsupportedOperation`）。
    - `open_writer(filename)` … 書き込み用（read は `io.UnsupportedOperation`）。

    どちらもバイナリ（bytes）だけを扱う。テキストの符号化は利用側の責務にし、ストアは
    バイト列の入出力に専念する（テスト時もモード分岐が無く意図が明確）。
    """

    async def open_reader(self, filename: str) -> FileObject: ...
    async def open_writer(self, filename: str) -> FileObject: ...


# ── KeyValueStore を FileStore として被せる汎用アダプタ ──


class _KvReadFileObject:
    """KVS から取得した全体バイト列を読み出す読み取り専用 [FileObject]。"""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    async def write(self, data: bytes) -> int:
        raise io.UnsupportedOperation("not writable")

    async def close(self) -> None:
        self._buf.close()

    async def __aenter__(self) -> _KvReadFileObject:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._buf.close()


class _KvWriteFileObject:
    """書き込みをメモリにバッファし、close 時に KVS へ全体 put する [FileObject]。"""

    def __init__(self, store: KeyValueStore, key: str) -> None:
        self._store = store
        self._key = key
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
        await self._store.put(self._key, self._buf.getvalue())
        self._buf.close()

    async def __aenter__(self) -> _KvWriteFileObject:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class KeyValueFileStore:
    """[KeyValueStore] を [FileStore]（open）として被せる汎用アダプタ。

    S3 / NATS のような全体 get/put のオブジェクトストアに open ベースのアクセスを与える
    （`KeyValueFileStore(S3KeyValueStore(...))` で S3 の FileStore になる）。真のストリーミング/
    ランダムアクセスではなく、read は全体取得、write は close 時に全体 put（メモリにバッファ）。
    backend 固有のストリーミング実装は [backends] の各 FileStore を参照。
    """

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    async def open_reader(self, filename: str) -> FileObject:
        data = await self._store.get(filename)
        if data is None:
            raise FileNotFoundError(filename)
        return _KvReadFileObject(data)

    async def open_writer(self, filename: str) -> FileObject:
        return _KvWriteFileObject(self._store, filename)
