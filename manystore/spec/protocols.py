"""spec.protocols — manystore の純粋な契約（型・Protocol・定数）。

実装（基底クラス・IO オブジェクト・原子的書き込み等のヘルパ）は同じ spec パッケージの
[base] モジュールに分離する（M073）。ここは runtime 実装を持たない契約面だけを置く
（型 [FileInfo] / [IfMatch] / [Verify]、Protocol 群、横断する既定値の定数）。
"""

from collections.abc import AsyncIterable, Iterator
from enum import IntFlag
from typing import Protocol

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
    `etag:str|None`（任意・CAS 用の不透明トークン＝S3=ETag/local=mtime_ns+size/dict=世代） /
    `sha256:str|None`（任意・**内容ハッシュ**＝download の整合性検証に使う。`etag` は backend ごと
    意味が違い横断ハッシュにできないので別フィールド。S3/NATS/dict は put 時に埋め head で返す／
    native メタを持たない local 等は None＝best-effort〔M013〕。`Verify` 参照）。
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


class Verify(IntFlag):
    """download の整合性検証ポリシー（**ビットフラグ**＝合成できる・M067）。

    取得データが `head()` の期待メタと一致するかを照合する。組み合わせで「size のみ」「hash も必須」
    「可能な限り」を選べる:
    - `SIZE` … 取得長を `FileInfo.size` と照合（全 backend が持つ＝常に有効）。
    - `HASH` … 取得 sha256 を `FileInfo.sha256` と照合。**メタに hash が無ければスキップ**
      （best-effort・`REQUIRE_HASH` 併用時のみ「hash 無し」を失敗にする）。
    - `REQUIRE_HASH` … `HASH` と併用し、メタに hash が無ければ**失敗**にする（「hash 必須」）。

    既定 `DEFAULT`（=`SIZE|HASH`）＝size は必ず照合・hash はあれば照合（無ければ素通り）。
    `STRICT`（=`SIZE|HASH|REQUIRE_HASH`）＝size と hash の両方を必須にする。`NONE`＝無検証。
    不一致は `IntegrityError`。
    """

    NONE = 0
    SIZE = 1
    HASH = 2
    REQUIRE_HASH = 4
    DEFAULT = SIZE | HASH
    STRICT = SIZE | HASH | REQUIRE_HASH


# ── async（一次） ──


class AsyncFileObject(Protocol):
    """`FileStore.open_reader`/`open_writer` が返すファイルオブジェクト（ストリーム）。"""

    async def read(self, size: int = -1) -> bytes: ...
    async def write(self, data: bytes) -> int: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> AsyncFileObject: ...
    async def __aexit__(self, *exc: object) -> None: ...


class AsyncBufferedStore(Protocol):
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


class AsyncStore(AsyncBufferedStore, Protocol):
    """**唯一の公開ストア型**（M071）＝put/get（buffered）＋ open_reader/open_writer（stream）。

    モデル: **Store = 値操作 + ストリーム IO**。`AsyncBufferedStore`（put/get/iter…）を継承し IO 2
    メソッドを足す。`AsyncBufferedStore` は「put/get だけ見たい」ための **view 型**として残す
    （Store はその上位＝すべての backend が満たす）。

    - `open_reader(filename)` … 読み取り用（write は `io.UnsupportedOperation`）。
    - `open_writer(filename)` … 書き込み用（read は `io.UnsupportedOperation`）。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject: ...
    async def open_writer(self, filename: str) -> AsyncFileObject: ...


#: 旧名 alias（非推奨・M071）＝公開型は `AsyncStore` に一本化。
AsyncStreamingStore = AsyncStore


# ── sync（async の同期版・突合用に 1:1 で並べる） ──


class SyncFileObject(Protocol):
    """[FileObject] の同期版（ストリーム）。"""

    def read(self, size: int = -1) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...
    def __enter__(self) -> SyncFileObject: ...
    def __exit__(self, *exc: object) -> None: ...


class SyncBufferedStore(Protocol):
    """[KeyValueStore] の同期版（put/get がメイン）。teardown は async `aclose` ↔ sync `close`。"""

    def put(
        self, key: str, value: bytes, *, if_match: IfMatch = None
    ) -> FileInfo: ...  # [AsyncBufferedStore.put] の同期版
    def create(
        self, key: str, value: bytes
    ) -> FileInfo: ...  # [AsyncBufferedStore.create] の同期版
    def head(self, key: str) -> FileInfo: ...  # [AsyncBufferedStore.head] の同期版
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


class SyncStore(SyncBufferedStore, Protocol):
    """`AsyncStore` の同期版＝**SyncBufferedStore + open_reader/open_writer**（M071）。"""

    def open_reader(self, filename: str) -> SyncFileObject: ...
    def open_writer(self, filename: str) -> SyncFileObject: ...


#: 旧名 alias（非推奨・M071）。
SyncStreamingStore = SyncStore
