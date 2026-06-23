"""service — protocol を manystore の [KeyValueStore] へ写す中核（[StorageService]）。

config の各 context を `create_key_value_store` で生成し、[SafeKeyValueStore] で包んで
（キー検証）保持する。一覧は backend の `iter_all()` を prefix で絞り込む（`list_all(limit)` は
prefix を持たないため）。各 context に [PollingWatcher] を 1 本張り、WS 購読へ fan-out する。

HTTP には一切依存しない＝この層だけで単体テストできる。
"""

from ..async_storage import KeyValueStore
from ..backends import create_key_value_store
from ..safe_path import SafeKeyValueStore, validate_safe_path
from .config import AppConfig
from .protocol import ContextInfo, EntryInfo
from .watcher import PollingWatcher


class ContextNotFound(KeyError):
    """指定された context が公開されていない。"""


class ReadOnlyContext(PermissionError):
    """書き込み不可（writable=false）の context に書き込もうとした。"""


class StorageService:
    """公開 context 群を保持し、protocol の操作を KeyValueStore に写すアプリ中核。"""

    def __init__(self, config: AppConfig, *, watch_interval: float = 1.0) -> None:
        self._config = config
        self._watch_interval = watch_interval
        self._stores: dict[str, KeyValueStore] = {}
        self._watchers: dict[str, PollingWatcher] = {}

    # ── ライフサイクル ──

    async def connect(self) -> None:
        """全 context のストアを生成・接続し、ウォッチャを起動する。"""
        for name, cc in self._config.contexts.items():
            raw = create_key_value_store(cc.backend, **cc.opts)  # type: ignore[arg-type]
            store = SafeKeyValueStore(raw)
            await store.connect()
            self._stores[name] = store
            watcher = PollingWatcher(store, name, interval=self._watch_interval)
            await watcher.start()
            self._watchers[name] = watcher

    async def aclose(self) -> None:
        """ウォッチャを止め、全ストアを閉じる。"""
        for watcher in self._watchers.values():
            await watcher.aclose()
        self._watchers.clear()
        for store in self._stores.values():
            await store.aclose()
        self._stores.clear()

    # ── 参照 ──

    def _store(self, context: str) -> KeyValueStore:
        try:
            return self._stores[context]
        except KeyError:
            raise ContextNotFound(context) from None

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
        """context 内のエントリを prefix で絞って返す（`iter_all()` を走査）。"""
        store = self._store(context)
        out: list[EntryInfo] = []
        async for info in store.iter_all():
            key = info["filename"]
            if prefix and not key.startswith(prefix):
                continue
            out.append(EntryInfo(key=key, size=info["size"]))
            if len(out) >= limit:
                break
        return out

    async def get(self, context: str, key: str) -> bytes | None:
        return await self._store(context).get(validate_safe_path(key))

    async def exists(self, context: str, key: str) -> bool:
        return await self._store(context).exists(validate_safe_path(key))

    async def put(self, context: str, key: str, value: bytes) -> None:
        self._require_writable(context)
        await self._store(context).put(validate_safe_path(key), value)

    async def delete(self, context: str, key: str) -> None:
        self._require_writable(context)
        await self._store(context).delete(validate_safe_path(key))

    def _require_writable(self, context: str) -> None:
        cc = self._config.contexts.get(context)
        if cc is None:
            raise ContextNotFound(context)
        if not cc.writable:
            raise ReadOnlyContext(context)
