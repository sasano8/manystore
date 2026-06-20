"""client — manystore.server の protocol を喋る Python クライアント層。

- [StorageClient]       … サーバ横断の薄い SDK（list_contexts / list_entries / get/put/delete）。
- [RemoteKeyValueStore] … 1 context を [KeyValueStore] として被せる（既存 read-only http_store の
  RW 版＝サーバ越しに put/delete もできる）。`_kv_copy` / `_kv_move` を再利用して cp/mv を満たす。

httpx を使う（本体依存に含む）。
"""

from .http_client import RemoteKeyValueStore, StorageClient

__all__ = ["StorageClient", "RemoteKeyValueStore"]
