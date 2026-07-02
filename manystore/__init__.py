"""manystore — 差し替え可能なバックエンドを持つストア群。

2 種のストア抽象を提供する（async を一次実装、sync ブリッジを同梱）:
- [KeyValueStore] … put/get がメインの値ストア（Local / S3 / NATS / HTTP バックエンド同梱）。
- [FileStore]     … `open` でファイルオブジェクト（[FileObject]）を取得するストリーム指向の抽象。

公開 API は facade `manystore.storage.kv`（値）/ `manystore.storage.file`（ファイル）に分け、
トップ `manystore` へフラットに再エクスポートする（`from manystore import ...` でも
`manystore.kv` / `manystore.file` 経由でも参照できる）。`__init__` 直下は stdlib のみに依存し、
重い backend（nats / aiobotocore / httpx 等）は各 backend のメソッド内で遅延 import する。
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
from .storage import file, kv, store
from .storage.backends import BackendSpec as BackendSpec
from .storage.backends import get_backend_spec as get_backend_spec
from .storage.backends import list_backends as list_backends
from .storage.backends import register_backend as register_backend
from .storage.file import *  # noqa: F403  （後方互換: ファイル群をトップにフラット再エクスポート）
from .storage.kv import *  # noqa: F403  （後方互換: KV 群をトップにフラット再エクスポート）
from .storage.store import *  # noqa: F403  （M071: 統合 Store 面をトップにフラット再エクスポート）

# グループ名前空間（`manystore.kv` / `manystore.file`）＋ 後方互換のフラット名。
# 共有名（FileInfo / validate_safe_path / UnsafePathError）が両グループに出るので重複を畳む。
# 例外は `manystore.exceptions` に集約し、基底・変換・主要ドメイン例外をトップにも公開する。
__all__ = list(
    dict.fromkeys(
        [
            "kv",
            "file",
            "store",
            *kv.__all__,
            *file.__all__,
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
