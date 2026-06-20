"""implement — ストレージ UI/サーバ機能の backend 非依存な実装層。

HTTP（server 層）にも Python SDK（client 層）にも依存しないコアを置く。ここを直接
KeyValueStore に対して単体テストできる（HTTP を立てずに service の振る舞いを検証する）。

- [protocol]  … server / client 共有の契約（dataclass。fastapi/pydantic 非依存）。
- [config]    … contexts マウント + views.featured を読み込む（tomllib）。
- [service]   … protocol を manystore の [KeyValueStore] へ写す中核（[StorageService]）。
- [watcher]   … ディレクトリ/ストアの変更を監視してイベント列にする（[PollingWatcher]）。
"""

from .config import AppConfig, ContextConfig, FeaturedView, load_config
from .protocol import ContextInfo, EntryInfo, Event
from .service import StorageService
from .watcher import PollingWatcher, Watcher

__all__ = [
    "AppConfig",
    "ContextConfig",
    "FeaturedView",
    "load_config",
    "ContextInfo",
    "EntryInfo",
    "Event",
    "StorageService",
    "Watcher",
    "PollingWatcher",
]
