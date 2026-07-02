"""ipfs backend — IPFS(Kubo) 越しのストア。**現状はスキャフォールド（中身は未実装）**。

> ⚠️ ここにあるのは「あるべき場所に置いた空定義 ＋ 接続ネタ」であり、メソッド本体は
> `NotImplementedError`。本実装は別タスクで詰める（IF と接続パラメータの形を先に固める）。

## アドレッシング — 2 つの乗せ方（IPFS は素直に KVS に乗らない）
IPFS はコンテンツアドレス（put すると鍵＝CID が「返ってくる」）で、`put(key, value)`＝**こちらが
鍵を決める** KVS モデルと逆。乗せ方は 2 択で、**MFS を主**・CID 直アクセスを従とする:

1. **MFS（Mutable File System・`/api/v0/files/*`）＝本命**。パス鍵・可変・列挙可で KVS に乗る:
   - `put(key, value)`  → `files/write?arg=/<mfs_root>/<key>&create=true&parents=true&truncate=true`
   - `get_or_raise(key)` → `files/read?arg=/<mfs_root>/<key>`（404/無 → `FileNotFoundError`）
   - `iter_all()`        → `files/ls?arg=/<mfs_root>&long=true`（再帰は自前で辿る）
   - `exists/delete/cp/mv` → `files/stat` / `files/rm` / `files/cp` / `files/mv`
   - 任意で `pin_on_write` のとき書き込み後に `files/stat` で CID を採り `pin/add`。
2. **content-addressed（CID 直）＝従**。`add`(→CID) / `cat`(CID→bytes)。鍵を選べず KVS と不整合。
   将来 key→CID の対応表ストアを別途持つ設計にするための **フック（[cid_get]/[cid_add]）だけ**残す。

## 依存
Kubo は HTTP API なので **httpx を流用**（http backend と同じ遅延 import）。新規重依存なし。
リモートのピン留めサービス（Pinata 等）は `token`（`Authorization` ヘッダ）で叩く。
"""

from collections.abc import AsyncIterator

from ...exceptions import UnsupportedOperation
from ...protocols import AsyncFileObject, BufferedStoreBase, FileInfo, IfMatch, _KvReadFileObject

# 既定の Kubo HTTP API（ローカルデーモン）と Gateway。
DEFAULT_API_URL = "http://127.0.0.1:5001"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"


def _todo(op: str) -> None:
    raise NotImplementedError(f"ipfs backend scaffold: {op} is not implemented yet")


class _IpfsBase:
    """IPFS 系ストアの共通部＝**接続ネタ（config）の置き場**と client 生成（遅延 import）。"""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        token: str = "",
        mfs_root: str = "/manystore",
        pin_on_write: bool = False,
        timeout: float = 30.0,
    ) -> None:
        # api_url      … Kubo HTTP API（`/api/v0/*` を叩く先）。
        # gateway_url  … 読みをゲートウェイ経由にする場合の `http://host:8080/ipfs/<cid>`。
        # token        … リモートピン留めサービス等の `Authorization` ヘッダ値（空なら付けない）。
        # mfs_root     … MFS 上の名前空間プレフィックス（全鍵をこの配下に置く）。
        # pin_on_write … 書き込み後に CID を pin するか（GC からの保護）。
        # timeout      … 1 リクエストのタイムアウト秒。
        self._api_url = api_url.rstrip("/")
        self._gateway_url = gateway_url.rstrip("/")
        self._token = token
        self._mfs_root = "/" + mfs_root.strip("/")
        self._pin_on_write = pin_on_write
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._token} if self._token else {}

    def _client(self):
        import httpx

        return httpx.AsyncClient(headers=self._headers(), timeout=self._timeout)

    def _mfs_path(self, key: str) -> str:
        """鍵 → MFS 絶対パス（`<mfs_root>/<key>`）。"""
        return f"{self._mfs_root}/{key.lstrip('/')}"

    async def connect(self) -> None:
        # 本実装では `POST /api/v0/files/mkdir?arg=<mfs_root>&parents=true` で root を用意する想定。
        _todo("connect")

    async def aclose(self) -> None:
        return None


class IpfsKeyValueStore(_IpfsBase, BufferedStoreBase):
    """IPFS(MFS) 越しの KVS スキャフォールド。primitive は `get_or_raise`（kv 寄り）。

    本体は未実装（`NotImplementedError`）。上の docstring の MFS エンドポイント対応に沿って詰める。
    """

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        _todo("put")  # files/write（create/parents/truncate）＋任意 pin

    async def get_or_raise(self, key: str) -> bytes:
        _todo("get_or_raise")  # files/read（無ければ FileNotFoundError へ正規化）

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        _todo("iter_all")  # files/ls を <mfs_root>/<prefix> から再帰（prefix 絞り込み込み）
        yield  # 未到達（async generator 化のため）

    async def exists(self, key: str) -> bool:
        _todo("exists")  # files/stat の有無

    async def delete(self, key: str) -> None:
        _todo("delete")  # files/rm

    async def cp(self, src: str, dst: str) -> None:
        _todo("cp")  # files/cp（同一 MFS 内）

    async def mv(self, src: str, dst: str) -> None:
        _todo("mv")  # files/mv

    # ── content-addressed（CID 直）フック＝従。key→CID 対応表を将来別途持つための足場。──
    async def cid_add(self, value: bytes) -> str:
        """bytes を `add` して CID を返す（鍵は選べない＝KVS の外側の操作）。"""
        _todo("cid_add")

    async def cid_get(self, cid: str) -> bytes:
        """CID から `cat` で bytes を取る。"""
        _todo("cid_get")


class IpfsFileStore(IpfsKeyValueStore):
    """IPFS(MFS) 越しの完全 [FileStore]（= [IpfsKeyValueStore] ＋ IO）。スキャフォールド。

    IPFS は **kv 寄り**（whole read/write が native）。open_reader は whole get の上に buffer 合成
    （[_KvReadFileObject]）、open_writer は本実装で files/write のストリーム化を検討（当面未実装）。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return _KvReadFileObject(await self.get_or_raise(filename))  # 欠損は FileNotFoundError

    async def open_writer(self, filename: str) -> AsyncFileObject:
        raise UnsupportedOperation("ipfs backend scaffold: open_writer not implemented yet")
