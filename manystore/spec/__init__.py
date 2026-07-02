"""spec — manystore の仕様（契約）と既定実装を束ねるパッケージ（M073）。

- [protocols] … 純粋な契約（型・Protocol・定数）。runtime 実装を持たない。
- [base]      … 契約の既定実装（基底クラス・IO オブジェクト・ヘルパ）。
- [exceptions]… ドメイン例外と problem 変換。

外からは `from manystore.spec import ...` で契約・実装・例外をフラットに参照できる
（`*` が拾わない `_` 始まりの実装ヘルパは明示再エクスポートする）。
"""

from .base import *  # noqa: F403
from .base import (
    _aclose_all as _aclose_all,
)
from .base import (
    _atomic_write_bytes as _atomic_write_bytes,
)
from .base import (
    _atomic_write_bytes_async as _atomic_write_bytes_async,
)
from .base import (
    _connect_all as _connect_all,
)
from .base import (
    _ensure_parent_async as _ensure_parent_async,
)
from .base import (
    _is_file_async as _is_file_async,
)
from .base import (
    _kv_copy as _kv_copy,
)
from .base import (
    _kv_move as _kv_move,
)
from .base import (
    _KvReadFileObject as _KvReadFileObject,
)
from .base import (
    _KvWriteFileObject as _KvWriteFileObject,
)
from .base import (
    _sha256_hex as _sha256_hex,
)
from .base import (
    _StoreBase as _StoreBase,
)
from .exceptions import *  # noqa: F403
from .protocols import *  # noqa: F403
