"""service — protocol を manystore の [KeyValueStore] へ写す中核（[StorageService]）。

HTTP の **context（第一階層）は [ArrayKeyValueStore] の mount に対応**する。config の各 context を
`create_key_value_store` で生成 → [SafeKeyValueStore]（キー検証）で包み ArrayStorage に `mount` し、
(context, key) を `<context>/<key>` キーへ合成して 1 本の合成ストアへ写す＝**振り分けは ArrayStorage
に委譲**（service は writable・メタ・watcher だけを上載せ）。一覧は ArrayStorage の `iter_all()`
（各 mount を `<name>/` 前置する横断列挙）を context で切り出し prefix 絞り。各 context に
[PollingWatcher] を 1 本張り WS 購読へ fan-out する。

HTTP には一切依存しない＝この層だけで単体テストできる。
"""

from ...storage.backends import create_key_value_store
from ...exceptions import ContextNotFound, ReadOnlyContext  # 集約先（後方互換で再エクスポート）
from ...protocols import iter_prefix as _iter_prefix
from ...storage.surfaces.array import ArrayKeyValueStore
from ...storage.surfaces.safe import SafeKeyValueStore
from .config import AppConfig
from .protocol import ContextInfo, EntryInfo
from .watcher import PollingWatcher

__all__ = ["StorageService", "ContextNotFound", "ReadOnlyContext"]


class StorageService:
    """公開 context 群を保持し、protocol の操作を KeyValueStore に写すアプリ中核。"""

    def __init__(self, config: AppConfig, *, watch_interval: float = 1.0) -> None:
        self._config = config
        self._watch_interval = watch_interval
        # context = 第一階層の合成ストア。振り分け・横断列挙・跨ぎ cp/mv は ArrayStorage に委譲。
        self._array = ArrayKeyValueStore()
        self._watchers: dict[str, PollingWatcher] = {}

    # ── ライフサイクル ──

    async def connect(self) -> None:
        """全 context のストアを生成して ArrayStorage に mount し、ウォッチャを起動する。"""
        for name, cc in self._config.contexts.items():
            raw = create_key_value_store(cc.backend, **cc.opts)  # type: ignore[arg-type]
            store = SafeKeyValueStore(raw)  # キー検証は mount したストア側で効く
            await self._array.mount(name, store)  # mount が connect も担う＝第一階層へ割り当て
            watcher = PollingWatcher(store, name, interval=self._watch_interval)
            await watcher.start()
            self._watchers[name] = watcher

    async def aclose(self) -> None:
        """ウォッチャを止め、ArrayStorage（＝全 mount）を閉じる。"""
        for watcher in self._watchers.values():
            await watcher.aclose()
        self._watchers.clear()
        await self._array.aclose()

    # ── 参照 ──

    def _require_context(self, context: str) -> None:
        """未公開の context を弾く（ArrayStorage の mount 表を正とする）。"""
        if context not in self._array.mounts():
            raise ContextNotFound(context)

    def _key(self, context: str, key: str) -> str:
        """HTTP の (context, key) を ArrayStorage の `<context>/<key>` キーへ合成する。"""
        self._require_context(context)
        return f"{context}/{key}"

    def list_contexts(self) -> list[ContextInfo]:
        return [
            ContextInfo(name=cc.name, backend=cc.backend, writable=cc.writable)
            for cc in self._config.contexts.values()
        ]

    def featured(self) -> list[dict[str, object]]:
        """ビュー重点設定（views.featured）を素の dict 列で返す（protocol の一部）。"""
        return [
            {
                "context": fv.context,
                "path": fv.path,
                "label": fv.label,
                "pin": fv.pin,
                "quick_write": fv.quick_write,
            }
            for fv in self._config.featured
        ]

    @property
    def default_context(self) -> str:
        return self._config.default_context

    def watcher(self, context: str) -> PollingWatcher:
        try:
            return self._watchers[context]
        except KeyError:
            raise ContextNotFound(context) from None

    # ── CRUD ──

    async def list_entries(
        self, context: str, prefix: str = "", limit: int = 1000
    ) -> list[EntryInfo]:
        """context 内を prefix 絞りで返す。

        prefix フィルタは **capability 経由**（M030）＝ `iter_prefix` ヘルパが backend の
        ネイティブ prefix 列挙（S3 `list_objects_v2(Prefix=…)`）を素通しし、無ければ
        `iter_all()`+startswith に汎用フォールバックする。ArrayStorage が第一セグメント
        （=context）で mount を絞るので、別 context は走査しない。`<context>/` を剥がして
        bucket 相対キーへ戻す。
        """
        self._require_context(context)
        scope = f"{context}/"
        out: list[EntryInfo] = []
        async for info in _iter_prefix(self._array, scope + prefix):
            key = info["filename"][len(scope) :]
            out.append(EntryInfo(key=key, size=info["size"]))
            if len(out) >= limit:
                break
        return out

    async def get_or_raise(self, context: str, key: str) -> bytes:
        """欠損なら `FileNotFoundError`（get の primitive・client/backend と同じ規約）。"""
        return await self._array.get_or_raise(self._key(context, key))

    async def get(self, context: str, key: str, default: bytes | None = None) -> bytes | None:
        return await self._array.get(self._key(context, key), default)

    async def exists(self, context: str, key: str) -> bool:
        return await self._array.exists(self._key(context, key))

    async def put(self, context: str, key: str, value: bytes) -> None:
        self._require_writable(context)
        await self._array.put(self._key(context, key), value)

    async def delete(self, context: str, key: str) -> None:
        self._require_writable(context)
        await self._array.delete(self._key(context, key))

    def _require_writable(self, context: str) -> None:
        cc = self._config.contexts.get(context)
        if cc is None:
            raise ContextNotFound(context)
        if not cc.writable:
            raise ReadOnlyContext(context)
