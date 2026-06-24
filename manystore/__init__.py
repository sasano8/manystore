"""manystore — 差し替え可能なバックエンドを持つストア群。

2 種のストア抽象を提供する（async を一次実装、sync ブリッジを同梱）:
- [KeyValueStore] … put/get がメインの値ストア（Local / S3 / NATS / HTTP バックエンド同梱）。
- [FileStore]     … `open` でファイルオブジェクト（[FileObject]）を取得するストリーム指向の抽象。

公開 API は **2 つの名前空間にグルーピング**して公開する:
- [manystore.kv]   … キーバリューストア（put/get がメインの値ストア）。
- [manystore.file] … ファイルストレージ（`open_reader`/`open_writer` の指向・バイナリ専用）。

トップ `manystore` は後方互換のため両グループをフラットにも再エクスポートする（`manystore.kv` /
`manystore.file` のどちらからでも、トップからでも import できる）。`__init__` 直下は stdlib のみに
依存し、重い backend（nats / aiobotocore / httpx 等）は各 backend のメソッド内で遅延 import する。
"""

from . import file, kv
from .exceptions import PROBLEM_JSON as PROBLEM_JSON
from .exceptions import ContextNotFound as ContextNotFound
from .exceptions import ManystoreError as ManystoreError
from .exceptions import NoSuchUpload as NoSuchUpload
from .exceptions import ReadOnlyContext as ReadOnlyContext
from .exceptions import UnsafePathError as UnsafePathError
from .exceptions import to_problem as to_problem
from .file import *  # noqa: F403  （後方互換: ファイル群をトップにフラット再エクスポート）
from .kv import *  # noqa: F403  （後方互換: KV 群をトップにフラット再エクスポート）

# グループ名前空間（`manystore.kv` / `manystore.file`）＋ 後方互換のフラット名。
# 共有名（FileInfo / validate_safe_path / UnsafePathError）が両グループに出るので重複を畳む。
# 例外は `manystore.exceptions` に集約し、基底・変換・主要ドメイン例外をトップにも公開する。
__all__ = list(
    dict.fromkeys(
        [
            "kv",
            "file",
            *kv.__all__,
            *file.__all__,
            "ManystoreError",
            "to_problem",
            "PROBLEM_JSON",
            "ContextNotFound",
            "ReadOnlyContext",
            "NoSuchUpload",
            "UnsafePathError",
        ]
    )
)
