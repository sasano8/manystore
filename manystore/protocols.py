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
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from pathlib import Path
from typing import Protocol

from .exceptions import ConflictError, NotFoundError, UnsupportedOperation

# ── spec / 既定値（横断する名前付き定数の正本・M044） ──
# 複数モジュールで共有される spec 値・既定値はここ 1 か所で定義し、各所は名前で参照する
# （直書きの重複＝drift を断つ）。S3 互換ゲートウェイの仕様由来値（max-keys/partNumber 範囲等）は
# その所有モジュールに局所化したまま＝ここには core 共通のものだけを置く。

#: list 系（`iter_all`/`list_all`・service・native REST）で limit を明示しないときの既定上限件数。
DEFAULT_LIST_LIMIT = 1000

#: HTTP 越し list の実上限。native REST は「無制限（limit=None）」を表現できないため、None 指定や
#: prefix 絞り込み時はこの件数を取得してからクライアント側で絞る（[RemoteKeyValueStore]）。
MAX_HTTP_LIST_FETCH = 10_000


class FileInfo(dict):
    """ファイルのメタ情報。**dict 互換**（subscript／`.get`／`== {...}`／JSON 化）＋ `is_absent()`。

    キー: `filename:str` / `size:int|None`（None=不在） / `modified_at:float|None`（任意） /
    `etag:str|None`（任意・CAS 用の不透明トークン＝S3=ETag/local=mtime_ns+size/dict=世代）。
    **`size=None` は不在**（存在しないキー）を表す＝`is_absent()` が True。put の `if_match` には
    head/head_or_absent の戻り（存在なら版一致を要求／不在 FileInfo（`FileInfo.absent()`）なら
    create-only）を渡す。

    TypedDict はメソッドを持てないため `dict` を継承する。`__init__` で **構築時の型**（filename/
    size/modified_at/etag）を付ける（実体は dict＝`info["x"]` で読める）。
    """

    def __init__(
        self,
        filename: str,
        size: int | None = None,
        modified_at: float | None = None,
        etag: str | None = None,
        **kwargs,
    ):
        # filename/size＋modified_at/etag を常に保持（未設定 None）。size=None が不在の標識。
        super().__init__(filename=filename, size=size, modified_at=modified_at, etag=etag, **kwargs)

    @classmethod
    def absent(cls, filename: str = "") -> FileInfo:
        """不在を表す [FileInfo]（size=None）。`put(if_match=...)` の create-only 指定に使う。"""
        return cls(filename=filename)

    def is_absent(self: FileInfo) -> bool:
        return self.get("size") is None


#: conditional put の条件。None=無条件（LWW）／不在 FileInfo（`is_absent()`）=create-only／
#: その他 FileInfo=その etag に一致を要求（update CAS）。
type IfMatch = FileInfo | None


# ── async（一次） ──


class AsyncFileObject(Protocol):
    """`FileStore.open_reader`/`open_writer` が返すファイルオブジェクト（ストリーム）。"""

    async def read(self, size: int = -1) -> bytes: ...
    async def write(self, data: bytes) -> int: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> AsyncFileObject: ...
    async def __aexit__(self, *exc: object) -> None: ...


class AsyncKeyValueStore(Protocol):
    # put は書いた値の安価な [FileInfo]（`{filename, size}`）を返す。`if_match` で **conditional
    # put（CAS）**: None=無条件（原子＋直列化の last-writer-wins）／不在 FileInfo（`is_absent()`）=
    # 不在を要求（create-only・既存なら ConflictError）／その他 FileInfo=その etag に一致を要求
    # （update CAS・不一致は Conflict）。
    # version は不透明トークンとして FileInfo（head が返す）に畳む＝呼び出し側は解釈しない。
    # 並行安全性は **put を持つストアの必須挙動**（conformancer が検証）。
    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo: ...
    # create は **新規作成専用**（既存なら [ConflictError]）。exists→put の **非原子**な利便メソッド
    # （並行下は TOCTOU で二重作成しうる）。原子版は `put(if_match=FileInfo.absent())`。
    async def create(self, key: str, value: bytes) -> FileInfo: ...
    # head は値でなく **メタ情報**（filename/size/modified_at/etag）を返す情報取得。update CAS の
    # version 読み口＝head の戻り FileInfo をそのまま `put(if_match=...)` に渡す。欠損は NotFound。
    async def head(self, key: str) -> FileInfo: ...
    # head_or_absent は head（存在）か 不在 FileInfo（size=None）を返す＝get の **メタ版**。戻りを
    # そのまま `put(if_match=...)` に渡せば **upsert を CAS 付きで**（不在→create／存在→update）。
    async def head_or_absent(self, key: str) -> FileInfo: ...
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

    def put(
        self, key: str, value: bytes, *, if_match: IfMatch = None
    ) -> FileInfo: ...  # [AsyncKeyValueStore.put] の同期版
    def create(
        self, key: str, value: bytes
    ) -> FileInfo: ...  # [AsyncKeyValueStore.create] の同期版
    def head(self, key: str) -> FileInfo: ...  # [AsyncKeyValueStore.head] の同期版
    def head_or_absent(self, key: str) -> FileInfo: ...  # 同期版
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
        # backend は put を override する＝LocalFileStore）。
        if if_match is not None:
            raise NotImplementedError("conditional put requires backend-native CAS; override put")
        async with await self.open_writer(key) as f:
            await f.write(value)
        return FileInfo(filename=key, size=len(value))


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
    """get→put で src を dst へコピーする汎用実装（src が無ければ NotFoundError）。"""
    data = await store.get(src)
    if data is None:
        raise NotFoundError(src)
    await store.put(dst, data)


async def _kv_move(store: AsyncKeyValueStore, src: str, dst: str) -> None:
    """copy→delete で src を dst へ移動する汎用実装（原子的ではない）。"""
    await _kv_copy(store, src, dst)
    await store.delete(src)


async def _connect_all(stores: Iterable[AsyncKeyValueStore]) -> None:
    """複数ストアを順に connect する。**途中失敗で確立済みを巻き戻して**から再送出する（M057）。

    合成ストア（Array/loadbalancer）や service の connect が、N 番目で失敗したときに 1..N-1 を
    接続したまま放置するとリーク（aclose は呼ばれない）。確立済みを best-effort で閉じてから元の
    例外を伝播させる（巻き戻し中の aclose 失敗は元の失敗を優先して握り潰す）。
    """
    connected: list[AsyncKeyValueStore] = []
    try:
        for store in stores:
            await store.connect()
            connected.append(store)
    except Exception:
        with contextlib.suppress(Exception):
            await _aclose_all(reversed(connected))  # 巻き戻しは best-effort（元の例外を優先）
        raise


async def _aclose_all(stores: Iterable[AsyncKeyValueStore]) -> None:
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
# 汎用アダプタ ── KVS↔FileStore（共有 FileObject を合成して IO を埋め合わせ／落とす）
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

    def __init__(self, store: AsyncKeyValueStore, key: str) -> None:
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

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        return await self._store.put(key, value, if_match=if_match)

    async def head(self, key: str) -> FileInfo:
        return await self._store.head(key)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        async for info in self._store.iter_all(limit, prefix):  # limit/prefix ごと下層へ素通し
            yield info

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

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        return await self._store.put(key, value, if_match=if_match)

    async def head(self, key: str) -> FileInfo:
        return await self._store.head(key)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        async for info in self._store.iter_all(limit, prefix):  # 下層 FileStore へ素通し
            yield info

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
