"""array storage — 複数の KeyValueStore を論理名（マウント先）配下に束ねる合成ストア。

`await mount(name, store)` で論理名に backend を割り当て（マウント時に backend を connect）、キー
`"<name>/<subkey>"` の先頭セグメントで振り分ける。論理名はディレクトリのように振る舞い、全 backend
を「論理名配下に存在しているかのように」横断できる（[KeyValueStore] を満たす）。

[DownloadCache] は ArrayStorage（等の KeyValueStore）を包み、`download` でローカルキャッシュへ取得
するラッパ層（キャッシュは常にローカル FS。リモート backend をローカルへ落として使う想定）。
"""

from collections.abc import AsyncIterator
from pathlib import Path

from .async_storage import (
    FileInfo,
    KeyValueStore,
    KeyValueStoreBase,
    _atomic_write_bytes,
    _kv_copy,
    _kv_move,
    _take,
)
from .async_storage import iter_prefix as _iter_prefix
from .safe_path import validate_safe_path

# ダウンロードキャッシュのデフォルト先（ホーム配下）。
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "manystore"


class ArrayKeyValueStore(KeyValueStoreBase):
    """論理名 → [KeyValueStore] のマウント表で複数 backend を束ねる合成 [KeyValueStore]。"""

    def __init__(self) -> None:
        self._mounts: dict[str, KeyValueStore] = {}

    async def mount(self, name: str, store: KeyValueStore) -> None:
        """論理名 `name` に backend を割り当て、その backend を connect する。

        name は単一セグメント（'/' を含まない）。マウント時に `store.connect()` を呼ぶので、
        到達不能なら例外（リトライ/timeout が要るなら connecting() で包んだ store を渡す）。
        """
        if not name or "/" in name:
            raise ValueError(f"mount name must be a single segment: {name!r}")
        await store.connect()
        self._mounts[name] = store

    async def unmount(self, name: str) -> KeyValueStore | None:
        """論理名を外し、その backend を aclose して返す（無ければ None）。"""
        store = self._mounts.pop(name, None)
        if store is not None:
            await store.aclose()
        return store

    def mounts(self) -> list[str]:
        """マウント済みの論理名を名前順で返す。"""
        return sorted(self._mounts)

    def _route(self, key: str) -> tuple[KeyValueStore, str]:
        """`<name>/<subkey>` を (backend, subkey) に分解する。"""
        name, sep, subkey = key.partition("/")
        if not sep or not subkey:
            raise KeyError(f"key must be '<mount>/<subkey>': {key!r}")
        store = self._mounts.get(name)
        if store is None:
            raise KeyError(f"no mount named {name!r}")
        return store, subkey

    async def put(self, key: str, value: bytes) -> None:
        store, subkey = self._route(key)
        await store.put(subkey, value)

    async def get_or_raise(self, key: str) -> bytes:
        store, subkey = self._route(key)  # 不明な mount は KeyError（欠損ではない）
        return await store.get_or_raise(subkey)

    async def iter_all(self) -> AsyncIterator[FileInfo]:
        # 各 backend のエントリを論理名で prefix して横断する。
        for name in sorted(self._mounts, reverse=True):
            async for info in self._mounts[name].iter_all():
                yield FileInfo(filename=f"{name}/{info['filename']}", size=info["size"])

    async def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        # capability 伝播（[SupportsPrefixListing]）。prefix の第一セグメントで mount を絞り、
        # 残り（subprefix）を mount 内のネイティブ iter_prefix へ委譲して S3 native を素通しする。
        name, sep, subprefix = prefix.partition("/")
        if sep:
            # `<mount>/<subprefix>`: 単一 mount へルーティング（無ければ空）。
            store = self._mounts.get(name)
            if store is None:
                return
            async for info in _iter_prefix(store, subprefix):
                yield FileInfo(filename=f"{name}/{info['filename']}", size=info["size"])
        else:
            # `/` 無し＝（部分）mount 名一致。prefix に '/' が無いとき
            # `<mount>/<sub>`.startswith(prefix) ⟺ <mount>.startswith(prefix) なので、
            # 該当 mount を丸ごと列挙すればよい（subprefix は空）。
            for mname in sorted(self._mounts, reverse=True):
                if not mname.startswith(prefix):
                    continue
                async for info in self._mounts[mname].iter_all():
                    yield FileInfo(filename=f"{mname}/{info['filename']}", size=info["size"])

    async def list_all(self, limit: int = 10) -> list[FileInfo]:
        return await _take(self.iter_all(), limit)

    async def exists(self, key: str) -> bool:
        # 論理名そのもの（ディレクトリ扱い）はマウントされていれば存在とみなす。
        if key in self._mounts:
            return True
        try:
            store, subkey = self._route(key)
        except KeyError:
            return False
        return await store.exists(subkey)

    async def delete(self, key: str) -> None:
        store, subkey = self._route(key)
        await store.delete(subkey)

    async def cp(self, src: str, dst: str) -> None:
        s_store, s_key = self._route(src)
        d_store, d_key = self._route(dst)
        if s_store is d_store:
            await s_store.cp(s_key, d_key)  # 同一 backend は native（S3 copy_object 等）
        else:
            await _kv_copy(self, src, dst)  # mount 跨ぎは get→put

    async def mv(self, src: str, dst: str) -> None:
        s_store, s_key = self._route(src)
        d_store, d_key = self._route(dst)
        if s_store is d_store:
            await s_store.mv(s_key, d_key)  # 同一 backend は native（local は原子的 rename）
        else:
            await _kv_move(self, src, dst)  # mount 跨ぎは copy→delete

    async def connect(self) -> None:
        for store in self._mounts.values():
            await store.connect()

    async def aclose(self) -> None:
        for store in self._mounts.values():
            await store.aclose()


class DownloadCache(KeyValueStoreBase):
    """[KeyValueStore]（典型的には [ArrayKeyValueStore]）を包み、`download` でローカルへ取得する層。

    KVS 操作は委譲しつつ、`download(key)` で値をローカルキャッシュへ落としてパスを返す（PyTorch の
    モデル DL 様）。キャッシュは常にローカル FS・sync。`cache_dir` は init で絶対パスへ固定
    （cwd が変わってもヒットさせるため。既定 `~/.cache/manystore`）。
    """

    def __init__(self, store: KeyValueStore, cache_dir: Path | str | None = None) -> None:
        self._store = store
        base = Path(cache_dir).expanduser() if cache_dir is not None else DEFAULT_CACHE_DIR
        self._cache_dir = base.resolve()

    async def put(self, key: str, value: bytes) -> None:
        await self._store.put(key, value)

    async def get_or_raise(self, key: str) -> bytes:
        return await self._store.get_or_raise(key)

    def iter_all(self) -> AsyncIterator[FileInfo]:
        return self._store.iter_all()

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        return _iter_prefix(self._store, prefix)  # 下層の capability を伝播（非対応は loud）

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

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    async def download(self, key: str, *, force: bool = False) -> Path:
        """`key` の値をローカルキャッシュへ取得してパスを返す。既にあれば再取得しない。

        `force=True` で取り直す。`key` は [validate_safe_path] で検証し、キャッシュディレクトリの
        外へ書かせない。キャッシュ済み判定は存在ベース（上流更新の自動無効化は未対応）。上流に
        無ければ FileNotFoundError。
        """
        safe = validate_safe_path(key)
        dst = self._cache_dir / safe
        if dst.is_file() and not force:
            return dst  # cache hit（存在ベース）
        data = await self._store.get_or_raise(key)  # 上流に無ければ FileNotFoundError
        dst.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(dst, data)  # 原子的に書く
        return dst
