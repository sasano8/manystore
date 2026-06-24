"""stores.base — ストアの **基底実装クラス**と backend 横断の共通ヘルパ（契約は [protocols]）。

契約（Protocol）は [manystore.protocols] に集約してある。ここには「実装」だけを置く:
- 基底クラス: [KeyValueStoreBase]（kv 寄り＝get_or_raise が primitive）/ [FileStoreBase]（file 寄り＝
  open_reader/open_writer が primitive・KVS 面は IO から導出）。**backend は native primitive 側の基底を
  継承する**（NATS/dict/HTTP=KeyValueStoreBase・Local=FileStoreBase）。
- 共通ヘルパ（`_atomic_write_bytes` / `_kv_copy` / `_kv_move` / prefix capability の
  [iter_prefix] ディスパッチ・[scan_prefix]）。
- 2 方向の汎用アダプタ（KVS→FileStore の [KeyValueFileStore] / FileStore→KVS の [KeyValueFromFileStore]）。
"""

import abc
import contextlib
import io
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

# 契約は [protocols] が唯一の置き場。ここでは型注釈・isinstance・導出のために import するだけで、
# 再エクスポートはしない（利用側は protocols から取る＝参照の二重化を避ける）。
from ..protocols import FileInfo, FileObject, FileStore, KeyValueStore, SupportsPrefixListing

__all__ = [
    "KeyValueStoreBase",
    "FileStoreBase",
    "KeyValueFileStore",
    "KeyValueFromFileStore",
    "iter_prefix",
    "scan_prefix",
]


class FileStoreBase(abc.ABC):
    """**file 寄り** ([FileStore]) backend の基底＝primitive は `open_reader`/`open_writer`（ストリーム）。

    KVS 面（get_or_raise/put/get）は **IO から導出**する＝get は open_reader で全体読み、put は
    open_writer で全体書き（**値境界でのみバッファ**。ストリーム性能は open_reader/open_writer を直接
    使えば得られる）。よって **[KeyValueStoreBase] は継承しない**（kv 寄りの buffered primitive とは
    逆向き）。filesystem-native な `LocalFileStore` のような「真実が IO 側」の backend が継承する。

    対して **kv 寄り** backend（NATS/dict/HTTP＝whole get/put が native でバッファが元から生じる）は
    [KeyValueStoreBase] を継承し、IO は whole の上に buffer 合成する。`open_reader`/`open_writer` は
    **`@abstractmethod`**＝未実装ならインスタンス化時点で `TypeError`（実装漏れに必ず気づく）。
    """

    @abc.abstractmethod
    async def open_reader(self, filename: str) -> FileObject:
        """読み取りストリームを開く。欠損は `FileNotFoundError`。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def open_writer(self, filename: str) -> FileObject:
        """書き込みストリームを開く。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    async def get(self, key: str, default: bytes | None = None) -> bytes | None:
        try:
            return await self.get_or_raise(key)
        except FileNotFoundError:
            return default

    async def get_or_raise(self, key: str) -> bytes:
        # open_reader（ストリーム primitive）で全体読み＝値境界でバッファ。
        async with await self.open_reader(key) as f:
            return await f.read()

    async def put(self, key: str, value: bytes) -> None:
        # open_writer（ストリーム primitive）で全体書き＝値境界でバッファ。
        async with await self.open_writer(key) as f:
            await f.write(value)


# TODO: あまり意味ないのでは？ protocols を使えばいいじゃん？
class KeyValueStoreBase(abc.ABC):
    """KVS の `get` 既定実装を与える基底（backend は `get_or_raise` だけ実装すればよい）。

    primitive は **`get_or_raise`**（キーが無ければ `FileNotFoundError` を上げる）。全体取得の
    `get(key, default=None)` は get_or_raise を捕捉して、欠損時に `default` を返す既定実装を
    ここで 1 か所だけ提供する（各 backend で try/except を重複させない）。

    `get_or_raise` は **`@abstractmethod`**。これを実装しないストアは **インスタンス化時点で
    `TypeError`** になる＝「関係するストアが primitive を実装し忘れた」ことに必ず気づける
    （[KeyValueStore] Protocol を部分的にしか満たさない実装が黙って通るのを防ぐ）。
    """

    @abc.abstractmethod
    async def get_or_raise(self, key: str) -> bytes:
        """キーの値を返す。欠損は `FileNotFoundError`。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    async def get(self, key: str, default: bytes | None = None) -> bytes | None:
        try:
            return await self.get_or_raise(key)
        except FileNotFoundError:
            return default


def iter_prefix(store: KeyValueStore, prefix: str) -> AsyncIterator[FileInfo]:
    """`store` の `prefix` 前方一致エントリを列挙する **capability ディスパッチ**。

    `store` が [SupportsPrefixListing]（`iter_prefix`）を持てばそれへ委譲する（S3=サーバ側
    `list_objects_v2(Prefix=…)` で絞る／サーバ側 prefix を持たない backend は [scan_prefix] で
    **明示的に opt-in** した scan 実装）。capability を持たなければ **暗黙フォールバックせず
    `NotImplementedError` で即座に失敗する**（暗黙の総なめは「prefix 非対応」という事実を隠して
    問題を埋もれさせるため＝fail-loud 方針）。ラッパ（[SafeKeyValueStore] / [ArrayKeyValueStore]）は
    このディスパッチ経由で内側へ委譲し、非対応はそのまま伝播させる。
    """
    if not isinstance(store, SupportsPrefixListing):
        raise NotImplementedError(
            f"{type(store).__name__} は prefix 列挙 capability（iter_prefix）を持たない。"
            " backend/ラッパに iter_prefix を実装する（サーバ側 prefix が無いなら scan_prefix で"
            " 明示的に opt-in）こと。暗黙の iter_all 総なめ fallback はしない（fail-loud）。"
        )
    return store.iter_prefix(prefix)


async def scan_prefix(store: KeyValueStore, prefix: str) -> AsyncIterator[FileInfo]:
    """`iter_all()` を総なめして `prefix` で絞る **明示的な scan 実装**ヘルパ。

    サーバ側 prefix を持たない backend（local / dict / nats 等）が自身の `iter_prefix` 内で
    **自ら opt-in** して使う（`def iter_prefix(self, prefix): return scan_prefix(self, prefix)`）。
    [iter_prefix] ディスパッチが行う暗黙フォールバックではなく、**各 backend が「prefix を scan で
    支える」と宣言する**点が要（fail-loud＝非対応は黙って scan に落とさず、対応は明示する）。
    """
    async for info in store.iter_all():
        if info["filename"].startswith(prefix):
            yield info


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


class KeyValueFileStore(KeyValueStoreBase):
    """[KeyValueStore] を [FileStore] として被せる汎用アダプタ＝**IO の埋め合わせ**。

    KVS は FileStore から open_reader/open_writer を除いた部分集合なので、KVS→FileStore は
    その 2 つを合成すれば済む（put/get/get_or_raise・iter_all/list_all/exists/delete/cp/mv・
    connect/aclose は下層 KVS へそのまま委譲＝流用）。例 `KeyValueFileStore(S3KeyValueStore(...))`＝
    S3 を FileStore 化。合成する IO は真のストリーミングではなく、read=全体取得・write=close で
    全体 put（メモリに
    バッファ）。backend 固有のストリーミング実装は [backends] の各 FileStore を参照。
    """

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    # ── 合成する IO（KVS に無い分の埋め合わせ） ──

    # TODO: KeyValueStore は open_reader, open_writer を持たないという整理でいいと思う。後はファイルストアと同じ。
    async def open_reader(self, filename: str) -> FileObject:
        return _KvReadFileObject(await self._store.get_or_raise(filename))

    async def open_writer(self, filename: str) -> FileObject:
        return _KvWriteFileObject(self._store, filename)

    # ── KVS 面は下層へ委譲（FileStore = KVS + IO の KVS 部分） ──

    async def put(self, key: str, value: bytes) -> None:
        await self._store.put(key, value)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    async def iter_all(self, limit: int | None = None) -> AsyncIterator[FileInfo]:
        async for info in self._store.iter_all(limit):  # 下層の async iter を limit ごと素通し
            yield info

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        return iter_prefix(self._store, prefix)  # 下層の capability をそのまま伝播（非対応は loud）

    async def list_all(self, limit: int | None = None) -> list[FileInfo]:
        return await self._store.list_all(limit)

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


class KeyValueFromFileStore(KeyValueStoreBase):
    """[FileStore] を [KeyValueStore] として被せる汎用アダプタ（[KeyValueFileStore] の逆向き）。

    **FileStore = KeyValueStore + IO** なので、FileStore→KVS は **IO（open_reader/open_writer）を
    落とすだけ**＝put/get/get_or_raise・iter/list/exists/delete/cp/mv・connect/aclose を下層
    FileStore へそのまま委譲（流用）する。`get(key, default=None)` は基底 [KeyValueStoreBase] が
    get_or_raise を捕捉して与える。

    用途: ローカルのように「真実の実装が FileStore 側」にある backend で、open_reader/open_writer を
    隠した KVS ビューを得る（`LocalKeyValueStore = KeyValueFromFileStore(LocalFileStore)`）。
    """

    def __init__(self, store: FileStore) -> None:
        self._store = store

    async def put(self, key: str, value: bytes) -> None:
        await self._store.put(key, value)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    async def iter_all(self, limit: int | None = None) -> AsyncIterator[FileInfo]:
        async for info in self._store.iter_all(limit):  # 下層 FileStore の async iter を素通し
            yield info

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        return iter_prefix(
            self._store, prefix
        )  # 下層 FileStore の capability を伝播（非対応は loud）

    async def list_all(self, limit: int | None = None) -> list[FileInfo]:
        return await self._store.list_all(limit)

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
