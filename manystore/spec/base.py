"""spec.base — [spec.protocols] の契約に対する既定実装（M073）。

基底クラス（[_StoreBase] / [BufferedStoreBase] / [StreamingStoreBase] /
[StreamableBufferedStoreBase]）、KV↔File アダプタ、IO オブジェクト、原子的書き込みや
接続一括などのヘルパを提供する。backend は native primitive 側の基底を継承する
（kv 寄りなら [BufferedStoreBase]、stream 寄りなら [StreamingStoreBase]、両 native なら
[StreamableBufferedStoreBase]）。契約は [spec.protocols] を単一の正本として参照する。
"""

import abc
import contextlib
import io
import os
import tempfile
from collections.abc import AsyncIterable, Iterable
from functools import partial
from pathlib import Path

import anyio.to_thread

from .exceptions import ConflictError, NotFoundError, UnsupportedOperation
from .protocols import (
    AsyncBufferedStore,
    AsyncFileObject,
    FileInfo,
    IfMatch,
)


def _sha256_hex(value: bytes) -> str:
    """値の sha256 を 16 進文字列で返す（`FileInfo.sha256` の正準形・M013／download 検証 M067）。"""
    import hashlib

    return hashlib.sha256(value).hexdigest()


# ════════════════════════════════════════════════════════════════════════════
# 既定実装（基底クラス）── backend は native primitive 側の基底を継承する
# ════════════════════════════════════════════════════════════════════════════


class _StoreBase(abc.ABC):
    """どの backend にも共通する store 操作の基底（[Store] の値 API の表面）。

    値寄り（[BufferedStoreBase]）と IO 寄り（[StreamingStoreBase]）の差は「どれを native と
    するか」だけで、**[Store] の値 API の表面（put/get/iter_all/list_all/exists/delete/
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
    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        """値を書き、[FileInfo]（`{filename, size}`）を返す。**サブクラス必須**（primitive）。

        `if_match` で conditional put（CAS）: None=無条件（原子＋直列化の LWW）／不在 FileInfo=不在
        を要求（既存なら Conflict）／その他 FileInfo=etag 一致を要求（不一致は Conflict）。
        並行安全性は **put を持つストアの必須挙動**（conformancer が検証）。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get_or_raise(self, key: str) -> bytes:
        """キーの値を返す。欠損は `NotFoundError`（FNF 派生）。**サブクラス必須**（primitive）。"""
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

    async def create(self, key: str, value: bytes) -> FileInfo:
        # 新規作成専用＝既存なら ConflictError。exists→put で組む **非原子の利便メソッド**
        # （cp/mv と同じく primitive から導出する派生・backend primitive ではない）。並行下では
        # exists と put の間に隙間があり TOCTOU で二重作成しうる＝**原子的な create は
        # `put(if_match=FileInfo.absent())`（local=os.link）が正本**。ここは利便・非原子。
        if await self.exists(key):
            raise ConflictError(f"key already exists: {key}")
        return await self.put(key, value)

    async def head(self, key: str) -> FileInfo:
        # 既定: 値を読んで size を得る派生（modified_at/etag は不明＝None）。native メタを持つ
        # backend は **head と put(if_match=) を対で override** して CAS トークンを埋める。欠損は
        # get_or_raise が NotFoundError を上げる。
        data = await self.get_or_raise(key)
        return FileInfo(filename=key, size=len(data), modified_at=None, etag=None)

    async def head_or_absent(self, key: str) -> FileInfo:
        # head（存在）か 不在 FileInfo（size=None）を返す派生＝get の メタ版（primitive ではない）。
        # `cond = head_or_absent(k); put(k, v, if_match=cond)` で upsert を CAS 付きで行える。
        try:
            return await self.head(key)
        except FileNotFoundError:
            return FileInfo.absent(key)

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


class BufferedStoreBase(_StoreBase):
    """**kv 寄り** backend の基底＝primitive は `put` / `get_or_raise`（whole get/put が native）。

    NATS/dict/HTTP/S3 のように「whole の取得・保存が native で、バッファが元から生じる」backend が
    継承する。共通表面（iter_all/exists/delete/connect/aclose の abstract と get/list_all/cp/mv の
    既定）は [_StoreBase] から継ぐ。

    **ストリーム IO（open_reader/open_writer）は whole の get/put から合成した既定実装を持つ**
    （read=全体取得・write=close で全体 put＝値境界でバッファ）。[StreamingStoreBase] が逆向き
    （IO→get/put）を内蔵するのと対称で、**両基底とも put/get＋open_* の全 Store 表面を備える**
    ＝別ラッパ無しで full Store になる（M071）。native なストリーミングを持つ backend（S3 multipart
    等）は open_reader/open_writer を override して真のストリーム性能を出す。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject:
        # whole get を読み取りストリームに見せる合成（真のストリームは native override で）。
        return _KvReadFileObject(await self.get_or_raise(filename))

    async def open_writer(self, filename: str) -> AsyncFileObject:
        # close で全体 put する合成 writer（all-or-nothing＝例外経路は確定しない）。
        return _KvWriteFileObject(self, filename)


class StreamingStoreBase(_StoreBase):
    """**IO 寄り**（Store の IO API）backend の基底＝primitive は `open_reader`/`open_writer`。

    値 API 面（get_or_raise/put）は **IO から導出**する＝get_or_raise は open_reader で全体読み、
    put は open_writer で全体書き（**値境界でのみバッファ**。ストリーム性能は
    open_reader/open_writer を直接使えば得られる）。`LocalStore` 等「真実が IO 側」の backend が
    継ぐ。iter_all/exists/delete/connect/aclose は依然 [_StoreBase] の abstract（backend が実装）。

    対して **kv 寄り** backend は [BufferedStoreBase] を継承し IO は whole の上に buffer 合成する。
    `open_reader`/`open_writer` は **`@abstractmethod`**＝未実装なら生成時に `TypeError`。
    """

    @abc.abstractmethod
    async def open_reader(self, filename: str) -> AsyncFileObject:
        """読み取りストリームを開く。欠損は `NotFoundError`（FNF 派生）。**サブクラス必須**。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def open_writer(self, filename: str) -> AsyncFileObject:
        """書き込みストリームを開く。**サブクラス必須**（primitive）。"""
        raise NotImplementedError

    async def get_or_raise(self, key: str) -> bytes:
        # open_reader（ストリーム primitive）で全体読み＝値境界でバッファ。
        async with await self.open_reader(key) as f:
            return await f.read()

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        # open_writer（ストリーム primitive）で全体書き＝値境界でバッファ。conditional put は
        # open_writer 由来では原子的 CAS を保証できない＝fail-loud（native CAS を持つ file 寄り
        # backend は put を override する＝LocalStore）。
        if if_match is not None:
            raise NotImplementedError("conditional put requires backend-native CAS; override put")
        async with await self.open_writer(key) as f:
            await f.write(value)
        return FileInfo(filename=key, size=len(value))


class StreamableBufferedStoreBase(_StoreBase):
    """**両軸 native** backend の基底＝put/get も open_* も **native**（合成なし・M071）。

    S3 のように whole（put_object/get_object）も streaming（multipart/range）も native な backend が
    継承する。`_StoreBase`（put/get_or_raise 等 abstract）に IO の abstract を足すだけ＝**4 つを
    native 実装させる**（[BufferedStoreBase]/[StreamingStoreBase] の片側合成に落とさない）。
    多重継承は使わない（両基底の合成が衝突）。「両方 native」を型で表すタグ。
    """

    @abc.abstractmethod
    async def open_reader(self, filename: str) -> AsyncFileObject:
        """読み取りストリームを開く。欠損は `NotFoundError`。**サブクラス必須**（native）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def open_writer(self, filename: str) -> AsyncFileObject:
        """書き込みストリームを開く。**サブクラス必須**（native）。"""
        raise NotImplementedError


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


# 同期 FS 操作の非同期版（syscall をワーカースレッドへオフロード＝event loop を塞がない）。
# local backend の `_offload` と同流儀。真の async disk IO は不採用（移植性・最小優先・M010）。


async def _is_file_async(path: Path) -> bool:
    """`path.is_file()`（stat）をスレッドへオフロードする。"""
    return await anyio.to_thread.run_sync(path.is_file)


async def _ensure_parent_async(path: Path) -> None:
    """`path` の親ディレクトリを作る（`mkdir(parents=True, exist_ok=True)` をオフロード）。"""
    await anyio.to_thread.run_sync(partial(path.parent.mkdir, parents=True, exist_ok=True))


async def _atomic_write_bytes_async(path: Path, data: bytes) -> None:
    """[_atomic_write_bytes] の非同期版（temp+replace の同期 IO をスレッドへオフロード）。"""
    await anyio.to_thread.run_sync(_atomic_write_bytes, path, data)


async def _kv_copy(store: AsyncBufferedStore, src: str, dst: str) -> None:
    """get→put で src を dst へコピーする汎用実装（src が無ければ NotFoundError）。"""
    data = await store.get(src)
    if data is None:
        raise NotFoundError(src)
    await store.put(dst, data)


async def _kv_move(store: AsyncBufferedStore, src: str, dst: str) -> None:
    """copy→delete で src を dst へ移動する汎用実装（原子的ではない）。"""
    await _kv_copy(store, src, dst)
    await store.delete(src)


async def _connect_all(stores: Iterable[AsyncBufferedStore]) -> None:
    """複数ストアを順に connect する。**途中失敗で確立済みを巻き戻して**から再送出する（M057）。

    合成ストア（Array/loadbalancer）や service の connect が、N 番目で失敗したときに 1..N-1 を
    接続したまま放置するとリーク（aclose は呼ばれない）。確立済みを best-effort で閉じてから元の
    例外を伝播させる（巻き戻し中の aclose 失敗は元の失敗を優先して握り潰す）。
    """
    connected: list[AsyncBufferedStore] = []
    try:
        for store in stores:
            await store.connect()
            connected.append(store)
    except Exception:
        with contextlib.suppress(Exception):
            await _aclose_all(reversed(connected))  # 巻き戻しは best-effort（元の例外を優先）
        raise


async def _aclose_all(stores: Iterable[AsyncBufferedStore]) -> None:
    """複数ストアを**全て** aclose する。1 つの失敗で残りを閉じ漏らさない（M057）。

    逐次 await だと先頭の aclose が例外を投げた時点で残りが閉じられずリークする。全件を試し、
    最初に起きた例外だけを最後に送出する（fail-loud は保ちつつ全部閉じる）。
    """
    first_error: Exception | None = None
    for store in stores:
        try:
            await store.aclose()
        except Exception as e:  # noqa: BLE001  全件閉じ切ってから最初の例外を送出（リーク防止）
            first_error = first_error or e
    if first_error is not None:
        raise first_error


# ════════════════════════════════════════════════════════════════════════════
# 共有 FileObject ── 基底が IO API を値 API から buffer 合成するための材（open_reader/open_writer）
# ════════════════════════════════════════════════════════════════════════════


class _KvReadFileObject:
    """KVS から取得した全体バイト列を読み出す読み取り専用 [FileObject]。"""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    async def write(self, data: bytes) -> int:
        raise UnsupportedOperation("not writable")

    async def close(self) -> None:
        self._buf.close()

    async def __aenter__(self) -> _KvReadFileObject:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._buf.close()


class _KvWriteFileObject:
    """書き込みをメモリにバッファし、close 時に KVS へ全体 put する [FileObject]。"""

    def __init__(self, store: AsyncBufferedStore, key: str) -> None:
        self._store = store
        self._key = key
        self._buf = io.BytesIO()
        self._closed = False

    async def read(self, size: int = -1) -> bytes:
        raise UnsupportedOperation("not readable")

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
        if exc[0] is not None:
            # 例外経路は中途バッファを確定しない（all-or-nothing＝local atomic writer と同契約）。
            self._closed = True
            self._buf.close()
            return
        await self.close()
