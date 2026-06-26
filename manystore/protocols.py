"""protocols — manystore の **契約と既定実装の唯一の置き場（源泉）**。

「インターフェイスはどこ？」「backend が継承／流用する既定実装はどこ？」が、ここ 1 ファイルを見れば
一意に定まる。**async↔sync の突合**も 1 ファイルで完結する。内容は 3 段:

1. **契約（Protocol）** … async 版と sync 版のストア抽象（実装を持たない型）。
2. **既定実装の基底クラス** … `FileStoreBase`（file 寄り＝open_reader/open_writer が
   primitive・KVS 面は IO から導出）/ `KeyValueStoreBase`（kv 寄り＝get_or_raise が
   primitive）。**backend は native primitive 側の基底を継承する**（NATS/dict/HTTP/S3=
   KeyValueStoreBase・Local=FileStoreBase）。
3. **汎用アダプタ＋共有ヘルパ** … 2 方向のアダプタ（KVS→FileStore の [KeyValueFileStore] /
   FileStore→KVS の [KeyValueFromFileStore]）、共有 IO・cp/mv・原子的書き込みのヘルパ。
   prefix 列挙は **別 capability ではなく `iter_all(prefix=…)`/`list_all(prefix=…)` 引数**に畳む
   （S3 はサーバ側 `Prefix=` で native・他は scan+filter が既定動作＝契約として明示）。

対応関係（async ↔ sync）:
- [KeyValueStore] ↔ [SyncKeyValueStore]（put/get がメインの値ストア。teardown は
  `aclose` ↔ `close`）。
- [FileStore] ↔ [SyncFileStore]（**= KeyValueStore + open_reader/open_writer**。包含を継承で表す）。
- [FileObject] ↔ [SyncFileObject]（ストリーム。`__aenter__/__aexit__` ↔ `__enter__/__exit__`）。

**FileStore = KeyValueStore + IO**（Protocol は包含を継承で表す）。「どちらを native primitive
として実装するか」は backend 次第＝**基底実装クラスの選択**（file 寄り=`FileStoreBase`／kv 寄り=
`KeyValueStoreBase`）で表現する。
"""

import abc
import contextlib
import io
import os
import tempfile
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from pathlib import Path
from typing import Protocol, TypedDict


class FileInfo(TypedDict):
    filename: str
    size: int


# ── async（一次） ──


class AsyncFileObject(Protocol):
    """`FileStore.open_reader`/`open_writer` が返すファイルオブジェクト（ストリーム）。"""

    async def read(self, size: int = -1) -> bytes: ...
    async def write(self, data: bytes) -> int: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> AsyncFileObject: ...
    async def __aexit__(self, *exc: object) -> None: ...


class AsyncKeyValueStore(Protocol):
    # put は書いた値の [FileInfo]（`{filename: key, size: len(value)}`）を返す＝**全 backend が
    # 追加 I/O なしに生成できる共通レスポンス**。revision/etag は共通でない（capability 行き）。
    async def put(self, key: str, value: bytes) -> FileInfo: ...
    async def get_or_raise(self, key: str) -> bytes: ...
    async def get(self, key: str, default: bytes | None = None) -> bytes | None: ...
    # iter_all/list_all は **全キーを平坦に**列挙する（'/' を含むネストキーも再帰的に＝1 階層だけ
    # ではない）。`limit` は件数上限（`None`=全件）。`prefix` は前方一致フィルタ（既定 `""`=全件）:
    # S3 はサーバ側 `Prefix=` で native に絞り、native の無い backend は scan+filter
    # （全件走査して startswith）で支える＝**契約上の既定動作**（暗黙フォールバックではない）。
    # 階層の 1 段だけを返す概念は持たない（KVS はフラット）。
    async def iter_all(
        self, limit: int | None = None, prefix: str = ""
    ) -> AsyncIterable[FileInfo]: ...
    async def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def cp(self, src: str, dst: str) -> None: ...
    async def mv(self, src: str, dst: str) -> None: ...
    async def connect(self) -> None: ...
    async def aclose(self) -> None: ...


class AsyncFileStore(AsyncKeyValueStore, Protocol):
    """[KeyValueStore] にストリーム IO（open_reader/open_writer）を足したストア（バイナリ専用）。

    モデル: **FileStore = KeyValueStore + {open_reader, open_writer}**。KVS 面（put/get/iter…・
    connect/aclose）は [KeyValueStore] からそのまま継承し、FileStore は方向が型に出る IO 2 メソッド
    だけを足す（= KeyValueStore は FileStore から IO を除いた部分集合）。

    - `open_reader(filename)` … 読み取り用（write は `io.UnsupportedOperation`）。
    - `open_writer(filename)` … 書き込み用（read は `io.UnsupportedOperation`）。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject: ...
    async def open_writer(self, filename: str) -> AsyncFileObject: ...


# ── sync（async の同期版・突合用に 1:1 で並べる） ──


class SyncFileObject(Protocol):
    """[FileObject] の同期版（ストリーム）。"""

    def read(self, size: int = -1) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...
    def __enter__(self) -> SyncFileObject: ...
    def __exit__(self, *exc: object) -> None: ...


class SyncKeyValueStore(Protocol):
    """[KeyValueStore] の同期版（put/get がメイン）。teardown は async `aclose` ↔ sync `close`。"""

    def put(self, key: str, value: bytes) -> FileInfo: ...  # [AsyncKeyValueStore.put] の同期版
    def get_or_raise(self, key: str) -> bytes: ...
    def get(self, key: str, default: bytes | None = None) -> bytes | None: ...
    def iter_all(self, limit: int | None = None, prefix: str = "") -> Iterator[FileInfo]: ...
    def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def cp(self, src: str, dst: str) -> None: ...
    def mv(self, src: str, dst: str) -> None: ...
    def connect(self) -> None: ...
    def close(self) -> None: ...


class SyncFileStore(SyncKeyValueStore, Protocol):
    """[FileStore] の同期版＝**SyncKeyValueStore + open_reader/open_writer**（包含を継承）。"""

    def open_reader(self, filename: str) -> SyncFileObject: ...
    def open_writer(self, filename: str) -> SyncFileObject: ...


# ════════════════════════════════════════════════════════════════════════════
# 既定実装（基底クラス）── backend は native primitive 側の基底を継承する
# ════════════════════════════════════════════════════════════════════════════


class _StoreBase(abc.ABC):
    """KVS / FileStore どちらの backend にも共通する store 操作の基底（[KeyValueStore] の表面）。

    kv 寄り（[KeyValueStoreBase]）と file 寄り（[FileStoreBase]）の差は「どれを native primitive と
    するか」だけで、**[KeyValueStore] Protocol の表面（put/get/iter_all/list_all/exists/delete/
    cp/mv/connect/aclose）は両者で同一**。その共通表面をここに 1 か所だけ定義する:

    - **abstract primitive**（派生が必ず実装＝未実装はインスタンス化時点で `TypeError`／fail-loud）:
      `put` / `get_or_raise` / `iter_all` / `exists` / `delete` / `connect` / `aclose`。
    - **既定実装**（primitive から導出。派生は必要なら上書き）: `get`（get_or_raise から）/
      `list_all`（iter_all から）/ `cp`・`mv`（get→put / copy→delete）。

    これで「基底が `get_or_raise` だけ abstract で残り 9 メソッドを宣言も強制もしない＝部分実装が
    黙って Protocol を破る」ドリフト（M043）を断つ。Protocol との網羅・シグネチャ一致は conformancer
    の `assert_base_protocol_parity` が機械的に保証する（基底↔Protocol の lockstep）。
    """

    # ── abstract primitive（未実装はインスタンス化時 TypeError＝fail-loud） ──

    @abc.abstractmethod
    async def put(self, key: str, value: bytes) -> FileInfo:
        """値を書き、[FileInfo]（`{filename, size}`）を返す。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_or_raise(self, key: str) -> bytes:
        """キーの値を返す。欠損は `FileNotFoundError`。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterable[FileInfo]:
        """全キーを平坦に列挙（`limit`=件数上限・`prefix`=前方一致）。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def exists(self, key: str) -> bool:
        """キーの存在を返す。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """キーを削除する。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def connect(self) -> None:
        """接続を確立する（接続不要な backend は no-op で実装）。**サブクラス必須**。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def aclose(self) -> None:
        """接続を閉じる（接続不要な backend は no-op で実装）。**サブクラス必須**。"""
        raise NotImplementedError

    # ── 既定実装（primitive から導出・派生は上書き可） ──

    async def get(self, key: str, default: bytes | None = None) -> bytes | None:
        # get_or_raise を捕捉し欠損時 default を返す（各 backend で try/except を重複させない）。
        try:
            return await self.get_or_raise(key)
        except FileNotFoundError:
            return default

    async def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]:
        return [info async for info in self.iter_all(limit, prefix)]

    async def cp(self, src: str, dst: str) -> None:
        await _kv_copy(self, src, dst)

    async def mv(self, src: str, dst: str) -> None:
        await _kv_move(self, src, dst)


class KeyValueStoreBase(_StoreBase):
    """**kv 寄り** backend の基底＝primitive は `put` / `get_or_raise`（whole get/put が native）。

    NATS/dict/HTTP/S3 のように「whole の取得・保存が native で、バッファが元から生じる」backend が
    継承する。共通表面（iter_all/exists/delete/connect/aclose の abstract と get/list_all/cp/mv の
    既定）は [_StoreBase] から継ぐ。ストリーム IO（open_reader/open_writer）が要るときは
    [KeyValueFileStore] で被せて FileStore 化する。
    """


class FileStoreBase(_StoreBase):
    """**file 寄り** ([FileStore]) backend の基底＝primitive は `open_reader`/`open_writer`。

    KVS 面（get_or_raise/put）は **IO から導出**する＝get_or_raise は open_reader で全体読み、put は
    open_writer で全体書き（**値境界でのみバッファ**。ストリーム性能は open_reader/open_writer を
    直接使えば得られる）。`LocalFileStore` 等「真実が IO 側」の backend が継ぐ。
    iter_all/exists/delete/connect/aclose は依然 [_StoreBase] の abstract（backend が実装）。

    対して **kv 寄り** backend は [KeyValueStoreBase] を継承し IO は whole の上に buffer 合成する。
    `open_reader`/`open_writer` は **`@abstractmethod`**＝未実装なら生成時に `TypeError`。
    """

    @abc.abstractmethod
    async def open_reader(self, filename: str) -> AsyncFileObject:
        """読み取りストリームを開く。欠損は `FileNotFoundError`。**サブクラス必須**(primitive)。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def open_writer(self, filename: str) -> AsyncFileObject:
        """書き込みストリームを開く。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    async def get_or_raise(self, key: str) -> bytes:
        # open_reader（ストリーム primitive）で全体読み＝値境界でバッファ。
        async with await self.open_reader(key) as f:
            return await f.read()

    async def put(self, key: str, value: bytes) -> FileInfo:
        # open_writer（ストリーム primitive）で全体書き＝値境界でバッファ。
        async with await self.open_writer(key) as f:
            await f.write(value)
        return {"filename": key, "size": len(value)}


# ════════════════════════════════════════════════════════════════════════════
# 共有ヘルパ ── cp/mv・原子的書き込み
# ════════════════════════════════════════════════════════════════════════════


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


async def _kv_copy(store: AsyncKeyValueStore, src: str, dst: str) -> None:
    """get→put で src を dst へコピーする汎用実装（src が無ければ FileNotFoundError）。"""
    data = await store.get(src)
    if data is None:
        raise FileNotFoundError(src)
    await store.put(dst, data)


async def _kv_move(store: AsyncKeyValueStore, src: str, dst: str) -> None:
    """copy→delete で src を dst へ移動する汎用実装（原子的ではない）。"""
    await _kv_copy(store, src, dst)
    await store.delete(src)


# ════════════════════════════════════════════════════════════════════════════
# 汎用アダプタ ── KVS↔FileStore（共有 FileObject を合成して IO を埋め合わせ／落とす）
# ════════════════════════════════════════════════════════════════════════════


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

    def __init__(self, store: AsyncKeyValueStore, key: str) -> None:
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
    全体 put（メモリにバッファ）。backend 固有のストリーミング実装は [backends] の各 FileStore を
    参照。
    """

    def __init__(self, store: AsyncKeyValueStore) -> None:
        self._store = store

    # ── 合成する IO（KVS に無い分の埋め合わせ） ──

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return _KvReadFileObject(await self._store.get_or_raise(filename))

    async def open_writer(self, filename: str) -> AsyncFileObject:
        return _KvWriteFileObject(self._store, filename)

    # ── KVS 面は下層へ委譲（FileStore = KVS + IO の KVS 部分） ──

    async def put(self, key: str, value: bytes) -> FileInfo:
        return await self._store.put(key, value)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        async for info in self._store.iter_all(limit, prefix):  # limit/prefix ごと下層へ素通し
            yield info

    async def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]:
        return await self._store.list_all(limit, prefix)

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

    def __init__(self, store: AsyncFileStore) -> None:
        self._store = store

    async def put(self, key: str, value: bytes) -> FileInfo:
        return await self._store.put(key, value)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        async for info in self._store.iter_all(
            limit, prefix
        ):  # 下層 FileStore へ limit/prefix 素通し
            yield info

    async def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]:
        return await self._store.list_all(limit, prefix)

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
