"""async storage — ストア抽象と共通ヘルパ（backend 非依存のコア）。

2 種のストア抽象を定義する:
- [KeyValueStore] … put/get がメインの値ストア（バイト列をキーで出し入れ）。
- [FileStore] … `open` でファイルオブジェクト（[FileObject]）を取得するストリーム指向の抽象。

具体的な backend（Local / S3 / NATS）の実装は [backends] サブパッケージに置く。ここには
抽象（Protocol）と、backend 横断で使う小さなヘルパ（`_take` / `_atomic_write_bytes` /
`_kv_copy` / `_kv_move`）、および 2 方向の汎用アダプタ（KVS→FileStore の [KeyValueFileStore] /
FileStore→KVS の [KeyValueFromFileStore]）だけを置く。
"""

import abc
import contextlib
import io
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable


class FileInfo(TypedDict):
    filename: str
    size: int


# ── Key-Value store（put/get がメイン） ──


class KeyValueStore(Protocol):
    async def put(self, key: str, value: bytes) -> None: ...
    async def get_or_raise(self, key: str) -> bytes: ...
    async def get(self, key: str, default: bytes | None = None) -> bytes | None: ...
    def iter_all(self) -> AsyncIterator[FileInfo]: ...
    # list_all は **全キーを平坦に**列挙する（'/' を含むネストキーも再帰的に＝1 階層だけではない）。
    # `limit` は安全のための件数上限。階層の 1 段だけを返す概念は持たない（KVS はフラット）。
    async def list_all(self, limit: int = 10) -> list[FileInfo]: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def cp(self, src: str, dst: str) -> None: ...
    async def mv(self, src: str, dst: str) -> None: ...
    async def connect(self) -> None: ...
    async def aclose(self) -> None: ...


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


async def _take(entries: AsyncIterator[FileInfo], limit: int) -> list[FileInfo]:
    """非同期イテレータから先頭 `limit` 件を集めて返す（各 backend の list 共通実装）。"""
    out: list[FileInfo] = []
    async for info in entries:
        out.append(info)
        if len(out) >= limit:
            break
    return out


@runtime_checkable
class SupportsPrefixListing(Protocol):
    """`prefix` 前方一致の列挙をネイティブに持つストアの **optional capability**。

    core IF（[KeyValueStore]）には載せない（最小・汎用に保つ＝原則1）。S3 のように
    サーバ側で prefix を絞れる backend は native 実装、サーバ側 prefix を持たない backend は
    [scan_prefix] で明示的に opt-in して、いずれも自身が capability を**宣言**する。ディスパッチ
    [iter_prefix] は capability を持たないストアでは暗黙フォールバックせず loud に失敗する。
    """

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]: ...


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


# ── File store（open でファイルオブジェクトを取得） ──


class FileObject(Protocol):
    """`FileStore.open` が返すファイルオブジェクト（ストリーム）。"""

    async def read(self, size: int = -1) -> bytes: ...
    async def write(self, data: bytes) -> int: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> FileObject: ...
    async def __aexit__(self, *exc: object) -> None: ...


class FileStore(KeyValueStore, Protocol):
    """[KeyValueStore] にストリーム IO（open_reader/open_writer）を足したストア（バイナリ専用）。

    モデル: **FileStore = KeyValueStore + {open_reader, open_writer}**。put/get/get_or_raise・
    iter_all/list_all/exists/delete/cp/mv・connect/aclose は KeyValueStore からそのまま継承（流用）
    し、FileStore は方向が型に出る IO 2 メソッドだけを足す。逆に言えば **KeyValueStore は FileStore
    から IO を除いた部分集合**。

    - `open_reader(filename)` … 読み取り用（write は `io.UnsupportedOperation`）。
    - `open_writer(filename)` … 書き込み用（read は `io.UnsupportedOperation`）。
    どちらもバイナリ（bytes）専用。テキストの符号化は利用側の責務にする。

    変換: **KVS→FileStore** は無い IO の埋め合わせが要る（[KeyValueFileStore] が get/put から
    open_reader/open_writer を合成）。**FileStore→KVS** は IO を落とすだけ
    （[KeyValueFromFileStore]＝残りはそのまま流用）。filesystem-native な [backends.LocalFileStore]
    が「真実の実装」の代表例。S3/NATS/HTTP の FileStore は IO 以外（put/get/iter 等）を段階的に
    備える（未実装なら呼び出し時に AttributeError／非対応エラー）。
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

    async def open_reader(self, filename: str) -> FileObject:
        return _KvReadFileObject(await self._store.get_or_raise(filename))

    async def open_writer(self, filename: str) -> FileObject:
        return _KvWriteFileObject(self._store, filename)

    # ── KVS 面は下層へ委譲（FileStore = KVS + IO の KVS 部分） ──

    async def put(self, key: str, value: bytes) -> None:
        await self._store.put(key, value)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    def iter_all(self) -> AsyncIterator[FileInfo]:
        return self._store.iter_all()

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        return iter_prefix(self._store, prefix)  # 下層の capability をそのまま伝播（非対応は loud）

    async def list_all(self, limit: int = 10) -> list[FileInfo]:
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

    def iter_all(self) -> AsyncIterator[FileInfo]:
        return self._store.iter_all()

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        return iter_prefix(
            self._store, prefix
        )  # 下層 FileStore の capability を伝播（非対応は loud）

    async def list_all(self, limit: int = 10) -> list[FileInfo]:
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
