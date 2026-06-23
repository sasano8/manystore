"""async storage — ストア抽象と共通ヘルパ（backend 非依存のコア）。

2 種のストア抽象を定義する:
- [KeyValueStore] … put/get がメインの値ストア（バイト列をキーで出し入れ）。
- [FileStore] … `open` でファイルオブジェクト（[FileObject]）を取得するストリーム指向の抽象。

具体的な backend（Local / S3 / NATS）の実装は [backends] サブパッケージに置く。ここには
抽象（Protocol）と、backend 横断で使う小さなヘルパ（`_take` / `_atomic_write_bytes` /
`_kv_copy` / `_kv_move`）、および 2 方向の汎用アダプタ（KVS→FileStore の [KeyValueFileStore] /
FileStore→KVS の [KeyValueFromFileStore]）だけを置く。
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
    async def get_or_raise(self, key: str) -> bytes: ...
    async def get(self, key: str, default: bytes | None = None) -> bytes | None: ...
    def iter(self) -> AsyncIterator[FileInfo]: ...
    async def list(self, limit: int = 10) -> list[FileInfo]: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def cp(self, src: str, dst: str) -> None: ...
    async def mv(self, src: str, dst: str) -> None: ...
    async def connect(self) -> None: ...
    async def aclose(self) -> None: ...


class KeyValueStoreBase:
    """KVS の `get` 既定実装を与える基底（backend は `get_or_raise` だけ実装すればよい）。

    primitive は **`get_or_raise`**（キーが無ければ `FileNotFoundError` を上げる）。全体取得の
    `get(key, default=None)` は get_or_raise を捕捉して、欠損時に `default` を返す既定実装を
    ここで 1 か所だけ提供する（各 backend で try/except を重複させない）。サブクラスは
    get_or_raise を override する。
    """

    async def get_or_raise(self, key: str) -> bytes:
        raise NotImplementedError

    async def get(self, key: str, default: bytes | None = None) -> bytes | None:
        try:
            return await self.get_or_raise(key)
        except FileNotFoundError:
            return default


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

    open_reader/open_writer に加え、ファイル名前空間の操作（iter/list/exists/delete/cp/mv）と
    ライフサイクル（connect/aclose）も契約に含む。これにより FileStore は「真実の実装」を担え、
    [KeyValueFromFileStore] で KVS ビューを派生できる（filesystem-native な
    [backends.LocalFileStore] が代表例）。純粋にストリーム入出力だけの backend（S3/NATS/HTTP）は
    この名前空間操作を段階的に備える（未実装なら呼び出し時に AttributeError／非対応エラー）。
    """

    async def open_reader(self, filename: str) -> FileObject: ...
    async def open_writer(self, filename: str) -> FileObject: ...
    def iter(self) -> AsyncIterator[FileInfo]: ...
    async def list(self, limit: int = 10) -> list[FileInfo]: ...
    async def exists(self, filename: str) -> bool: ...
    async def delete(self, filename: str) -> None: ...
    async def cp(self, src: str, dst: str) -> None: ...
    async def mv(self, src: str, dst: str) -> None: ...
    async def connect(self) -> None: ...
    async def aclose(self) -> None: ...


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


class KeyValueFromFileStore(KeyValueStoreBase):
    """[FileStore] を [KeyValueStore] として被せる汎用アダプタ（[KeyValueFileStore] の逆向き）。

    get/put は下層の `open_reader` / `open_writer` 越しの**全体 read / 全体 write**＝KV 層で
    バッファする（「みせかけのストリーム」。真のストリーム性は下層 FileStore を直接使う）。
    iter/list/exists/delete/cp/mv は下層 FileStore のメソッドへ**素通し委譲**するので、下層は
    これらを提供している前提（filesystem-native な [backends.LocalFileStore] 等）。純粋に
    `open_reader`/`open_writer` だけの FileStore（S3/NATS/HTTP）に被せた場合は get/put のみ有効。

    get の primitive は `get_or_raise`＝下層 `open_reader`（欠損で `FileNotFoundError`）を
    コンテキストマネージャで開いて全体 read する。`get(key, default=None)` は基底
    [KeyValueStoreBase] が get_or_raise を捕捉して与える。

    用途: ローカルのように「真実の実装が FileStore 側」にある backend で、KV ビューをそこから
    派生させる（実装の二重持ちを避ける）。
    """

    def __init__(self, store: FileStore) -> None:
        self._store = store

    async def put(self, key: str, value: bytes) -> None:
        async with await self._store.open_writer(key) as w:
            await w.write(value)  # close（__aexit__）で確定＝下層の原子性に従う

    async def get_or_raise(self, key: str) -> bytes:
        # 下層 open_reader を CM で開く（欠損なら FileNotFoundError が伝播）。
        async with await self._store.open_reader(key) as r:
            return await r.read()

    def iter(self) -> AsyncIterator[FileInfo]:
        return self._store.iter()

    async def list(self, limit: int = 10) -> list[FileInfo]:
        return await self._store.list(limit)

    async def exists(self, key: str) -> bool:
        return await self._store.exists(key)

    async def delete(self, key: str) -> None:
        await self._store.delete(key)

    async def cp(self, src: str, dst: str) -> None:
        await self._store.cp(src, dst)

    async def mv(self, src: str, dst: str) -> None:
        await self._store.mv(src, dst)

    async def connect(self) -> None:
        await self._store.connect()

    async def aclose(self) -> None:
        await self._store.aclose()
