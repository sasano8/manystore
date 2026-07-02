"""client — manystore.server の API を喋る Python クライアント層（manystore API 前提）。

汎用 HTTP backend（`backends/http_store.py` の単純な GET クライアント）とは別。manystore-server が
公開する protocol を前提にしたクライアントを置く:

- [ManystoreClient]     … manystore.server の API を呼ぶ薄い SDK
  （list_contexts / list_entries / get / put / delete）。
- [RemoteKeyValueStore] … 1 context を [KeyValueStore] として被せる（既存 read-only http_store の
  RW 版＝サーバ越しに put/delete もできる）。`_kv_copy` / `_kv_move` を再利用して cp/mv を満たす。

httpx を使う（本体依存に含む）。
"""

from .remote import ManystoreClient, RemoteKeyValueStore, RemoteStore

__all__ = ["ManystoreClient", "RemoteStore", "RemoteKeyValueStore"]
