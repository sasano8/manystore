"""manystore — 差し替え可能なバックエンドを持つストア群。

1 つの Store（[Store]）を提供する（async を一次実装、sync ブリッジを同梱）:
- 値 API … put/get がメインの値操作（Local / S3 / NATS / HTTP バックエンド同梱）。
- IO API … `open_reader`/`open_writer` でファイルオブジェクト（[FileObject]）を取得する
  ストリーム指向の面。

公開 API は統合 facade `manystore.storage.store`（M071＝put/get も open_* も持つ 1 つの Store）に
集約し、トップ `manystore` へフラットに再エクスポートする（`from manystore import ...` でも
`manystore.store` 経由でも参照できる）。`__init__` 直下は stdlib のみに依存し、重い backend
（nats / aiobotocore / httpx 等）は各 backend のメソッド内で遅延 import する。
"""

from .spec.exceptions import PROBLEM_JSON as PROBLEM_JSON
from .spec.exceptions import ContextNotFound as ContextNotFound
from .spec.exceptions import IntegrityError as IntegrityError
from .spec.exceptions import ManystoreError as ManystoreError
from .spec.exceptions import NoSuchUpload as NoSuchUpload
from .spec.exceptions import NotFoundError as NotFoundError
from .spec.exceptions import ReadOnlyContext as ReadOnlyContext
from .spec.exceptions import UnsafePathError as UnsafePathError
from .spec.exceptions import to_problem as to_problem
from .storage import store
from .storage.backends import BackendSpec as BackendSpec
from .storage.backends import get_backend_spec as get_backend_spec
from .storage.backends import list_backends as list_backends
from .storage.backends import register_backend as register_backend
from .storage.store import *  # noqa: F403  （M071: 統合 Store 面をトップにフラット再エクスポート）

# 統合 facade `manystore.store` の名前空間＋そのフラット名をトップに公開（M071）。
# 例外は `manystore.spec.exceptions` に集約し、基底・変換・主要ドメイン例外をトップにも公開する。
__all__ = list(
    dict.fromkeys(
        [
            "store",
            *store.__all__,
            "ManystoreError",
            "to_problem",
            "PROBLEM_JSON",
            "ContextNotFound",
            "ReadOnlyContext",
            "NoSuchUpload",
            "UnsafePathError",
            "IntegrityError",
            "BackendSpec",
            "register_backend",
            "get_backend_spec",
            "list_backends",
        ]
    )
)
