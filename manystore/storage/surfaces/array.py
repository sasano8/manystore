"""array storage — 複数の KeyValueStore を論理名（マウント先）配下に束ねる合成ストア。

`await mount(name, store)` で論理名に backend を割り当て（現状は**登録のみ**＝I/O なし。非同期 IF は
将来の動的マウント余地）、キー `"<name>/<subkey>"` の先頭セグメントで振り分ける。接続は別途＝合成
ストアの `connect()`（全 mount を
connect）か、顔の入口 [open_async_array_store]（mount 群を connect する CM）が一括で担う（mount は
登録と接続の二重責務を持たない）。論理名はディレクトリのように振る舞い、全 backend を「論理名配下に
存在しているかのように」横断できる（[KeyValueStore] を満たす）。

[DownloadCache] は ArrayStorage（等の KeyValueStore）を包み、`download` でローカルキャッシュへ取得
するラッパ層（キャッシュは常にローカル FS。リモート backend をローカルへ落として使う想定）。
"""

from collections.abc import AsyncIterator
from pathlib import Path

from ...spec import (
    AsyncBufferedStore,
    BufferedStoreBase,
    FileInfo,
    IfMatch,
    Verify,
    _aclose_all,
    _atomic_write_bytes_async,
    _connect_all,
    _ensure_parent_async,
    _is_file_async,
    _kv_copy,
    _kv_move,
)
from ...spec.exceptions import IntegrityError
from .safe import validate_safe_path


def _verify_download(info: FileInfo, data: bytes, policy: Verify) -> None:
    """取得 `data` を `head()` の期待メタ `info` と照合する（不一致は `IntegrityError`・M067）。

    `SIZE` … 長さを `info.size` と照合。`HASH` … sha256 を `info.sha256` と照合（メタに無ければ
    best-effort でスキップ。ただし `REQUIRE_HASH` 併用時は「hash 無し」を失敗にする）。
    """
    if policy & Verify.SIZE:
        expected = info.get("size")
        if expected is not None and len(data) != expected:
            raise IntegrityError(
                f"{info.get('filename')!r}: size 不一致（取得 {len(data)} / 期待 {expected}）"
            )
    if policy & Verify.HASH:
        want = info.get("sha256")
        if want is None:
            if policy & Verify.REQUIRE_HASH:
                raise IntegrityError(
                    f"{info.get('filename')!r}: hash 未提供（メタに sha256 が無い・REQUIRE_HASH）"
                )
        else:
            import hashlib

            got = hashlib.sha256(data).hexdigest()
            if got != want:
                raise IntegrityError(
                    f"{info.get('filename')!r}: sha256 不一致（取得 {got} / 期待 {want}）"
                )


# ダウンロードキャッシュのデフォルト先（ホーム配下）。
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "manystore"


class ArrayStore(BufferedStoreBase):
    """論理名 → [KeyValueStore] のマウント表で複数 backend を束ねる合成 [KeyValueStore]。"""

    def __init__(self) -> None:
        self._mounts: dict[str, AsyncBufferedStore] = {}

    async def mount(self, name: str, store: AsyncBufferedStore) -> None:
        """論理名 `name` に backend を割り当てる（現状は**登録のみ**＝I/O なし）。

        **インターフェースは非同期**にしてある＝将来の動的マウントで「connect＋登録」を
        `asyncio.Lock` で直列化する余地を残すため（現状の本体は `await` 点を持たない＝原子的）。
        name は単一セグメント（'/' を含まない）。connect はしない＝接続は合成ストアの `connect()`
        か顔の [open_async_array_store] が一括で担う。
        """
        if not name or "/" in name:
            raise ValueError(f"mount name must be a single segment: {name!r}")
        self._mounts[name] = store

    async def unmount(self, name: str) -> AsyncBufferedStore | None:
        """論理名を外して登録解除し、外した backend を返す（無ければ None。**aclose はしない**）。

        mount と対称（非同期 IF・現状は登録解除のみ・I/O なし）。外した backend の `aclose` は
        呼び出し側の責務。
        """
        return self._mounts.pop(name, None)

    def mounts(self) -> list[str]:
        """マウント済みの論理名を名前順で返す。"""
        return sorted(self._mounts)

    def _route(self, key: str) -> tuple[AsyncBufferedStore, str]:
        """`<name>/<subkey>` を (backend, subkey) に分解する。"""
        name, sep, subkey = key.partition("/")
        if not sep or not subkey:
            raise KeyError(f"key must be '<mount>/<subkey>': {key!r}")
        store = self._mounts.get(name)
        if store is None:
            raise KeyError(f"no mount named {name!r}")
        return store, subkey

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        store, subkey = self._route(key)
        info = await store.put(subkey, value, if_match=if_match)
        # iter_all と同じく論理名で prefix し直して外向きキーで返す（subkey ではなく key）。
        return FileInfo(filename=key, size=info["size"])

    async def head(self, key: str) -> FileInfo:
        store, subkey = self._route(key)  # 不明な mount は KeyError（欠損ではない）
        info = await store.head(subkey)
        # version トークン（modified_at/etag）と sha256 は下層のまま透過し、filename だけ論理名へ。
        return FileInfo(
            filename=key,
            size=info["size"],
            modified_at=info.get("modified_at"),
            etag=info.get("etag"),
            sha256=info.get("sha256"),
        )

    async def get_or_raise(self, key: str) -> bytes:
        store, subkey = self._route(key)  # 不明な mount は KeyError（欠損ではない）
        return await store.get_or_raise(subkey)

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # 各 backend のエントリを論理名で prefix して横断する。limit は横断の総件数で打ち切る。
        # prefix 絞り込みは第一セグメントで mount を絞り、残り（subprefix）を mount の iter_all へ
        # 委譲して下層 native（S3 サーバ側 Prefix=）を素通しする（mount 外は走査しない）。
        count = 0

        async def _emit(mname: str, subprefix: str) -> AsyncIterator[FileInfo]:
            # `<mname>/<subprefix>` の列挙を論理名で再前置して返す。
            async for info in self._mounts[mname].iter_all(prefix=subprefix):
                yield FileInfo(filename=f"{mname}/{info['filename']}", size=info["size"])

        if not prefix:
            # 全件: 全 mount を名前降順で横断。
            sources = [(m, "") for m in sorted(self._mounts, reverse=True)]
        else:
            name, sep, subprefix = prefix.partition("/")
            if sep:
                # `<mount>/<subprefix>`: 単一 mount へルーティング（無ければ空）。
                sources = [(name, subprefix)] if name in self._mounts else []
            else:
                # `/` 無し＝（部分）mount 名一致。`<mount>/<sub>`.startswith(prefix) ⟺
                # <mount>.startswith(prefix) なので該当 mount を丸ごと列挙（subprefix は空）。
                sources = [
                    (m, "") for m in sorted(self._mounts, reverse=True) if m.startswith(prefix)
                ]

        for mname, subprefix in sources:
            async for info in _emit(mname, subprefix):
                if limit is not None and count >= limit:
                    return
                yield info
                count += 1

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

    # cp/mv の「同一 backend」判定は store オブジェクトの identity（`is`）で行う（M064）。
    # 「同一 mount／同一ストアを別名で 2 度 mount」のときだけ native（S3 copy_object・local 原子
    # rename 等）を使い、それ以外は get→put に落とす**保守的**な設計。同一物理 backend を別ラッパ
    # （別 SafeStore 等）で 2 度包んだ別 mount どうしは `is`=False で native を取りこぼすが
    # 意図的: 別 mount は論理的に別コンテキストで subkey 名前空間の一致を Array は保証できず、native
    # cp は意味論的に危険ゆえ安全側に倒す。物理同一性判定を IF に足すのは最小原則に反する（YAGNI）。
    # 確実に native にしたいなら同一ストアオブジェクトを 2 名で mount する。

    async def cp(self, src: str, dst: str) -> None:
        s_store, s_key = self._route(src)
        d_store, d_key = self._route(dst)
        if s_store is d_store:
            await s_store.cp(s_key, d_key)  # 同一ストアオブジェクト＝native（S3 copy_object 等）
        else:
            await _kv_copy(self, src, dst)  # mount 跨ぎは get→put（上記の保守設計）

    async def mv(self, src: str, dst: str) -> None:
        s_store, s_key = self._route(src)
        d_store, d_key = self._route(dst)
        if s_store is d_store:
            await s_store.mv(s_key, d_key)  # 同一ストアオブジェクト＝native（local は原子 rename）
        else:
            await _kv_move(self, src, dst)  # mount 跨ぎは copy→delete（上記の保守設計）

    async def connect(self) -> None:
        # 途中失敗で確立済み mount を巻き戻す（部分接続を残さない・M057）。
        await _connect_all(self._mounts.values())

    async def aclose(self) -> None:
        # 1 つの aclose 失敗で残り mount を閉じ漏らさない（全件試行・M057）。
        await _aclose_all(self._mounts.values())


class DownloadCache(BufferedStoreBase):
    """[KeyValueStore]（典型的には [ArrayStore]）を包み、`download` でローカルへ取得する層。

    KVS 操作は委譲しつつ、`download(key)` で値をローカルキャッシュへ落としてパスを返す（PyTorch の
    モデル DL 様）。キャッシュは常にローカル FS・sync。`cache_dir` は init で絶対パスへ固定
    （cwd が変わってもヒットさせるため。既定 `~/.cache/manystore`）。
    """

    def __init__(self, store: AsyncBufferedStore, cache_dir: Path | str | None = None) -> None:
        self._store = store
        base = Path(cache_dir).expanduser() if cache_dir is not None else DEFAULT_CACHE_DIR
        self._cache_dir = base.resolve()

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

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    async def download(
        self, key: str, *, verify: Verify = Verify.DEFAULT, force: bool = False
    ) -> Path:
        """`key` の値をローカルキャッシュへ取得してパスを返す。既にあれば再取得しない。

        `force=True` で取り直す。`key` は [validate_safe_path] で検証し、キャッシュディレクトリの
        外へ書かせない。キャッシュ済み判定は存在ベース（上流更新の自動無効化は未対応）。上流に
        無ければ FileNotFoundError。

        `verify`（[Verify] ビットフラグ・既定 `DEFAULT`＝size 必須・hash あれば照合）で取得データを
        `head()` の期待メタと照合し、不一致は `IntegrityError`。**検証してから書く**ので cache に
        入るのは検証済みのみ＝cache hit は再検証しない。`Verify.NONE` なら head() も引かない。
        """
        safe = validate_safe_path(key)
        dst = self._cache_dir / safe
        # 同期 FS 操作（stat/mkdir/write）は非同期ヘルパでスレッドへ逃がす（非ブロック・M063）。
        if not force and await _is_file_async(dst):
            return dst  # cache hit（書込時に検証済み）
        data = await self._store.get_or_raise(key)  # 上流に無ければ FileNotFoundError
        if verify != Verify.NONE:
            _verify_download(await self._store.head(key), data, verify)  # 検証してから書く
        await _ensure_parent_async(dst)
        await _atomic_write_bytes_async(dst, data)  # 原子的に書く
        return dst
