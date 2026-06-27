"""storage sync — 2 つのストア間の **片方向同期（one-way mirror）**。

source（正）→ sink への完全同期を **集合差 reconcile**（両側を列挙して存在有無を突合）で行う。
source を常に正とし sink→source の書き戻しはしない（= one-way の不変条件）。詳細は [StorageMirror]。
"""

from .mirror import StorageMirror, SyncPlan, SyncResult

__all__ = ["StorageMirror", "SyncPlan", "SyncResult"]
