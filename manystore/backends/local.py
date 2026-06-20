"""local backend — ローカルファイルシステム実装（KVS / FileStore）。

書き込みは temp+rename で原子的（all-or-nothing）。パスは init で絶対パスへ固定（cd 非依存）。
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from ..async_storage import FileInfo, FileObject, _atomic_write_bytes, _kv_copy, _take


class LocalKeyValueStore:
    def __init__(self, directory: Path) -> None:
        # 初期化時に絶対パスへ解決して固定する。実行中に cwd が cd で変わっても
        # 挙動が変わらないようにするため（相対パスのまま保持しない）。
        self._dir = Path(directory).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, value: bytes) -> None:
        # キーに '/' を含む場合に備えて親ディレクトリを作る（s3/nats の
        # フラットキー規約＝任意の '/' を含むキーをそのまま置けるのに合わせる）。
        path = self._dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        # temp+rename で原子的に書く（途中失敗で既存値が壊れない＝all-or-nothing）。
        _atomic_write_bytes(path, value)

    async def get(self, key: str) -> bytes | None:
        path = self._dir / key
        if not path.is_file():
            return None
        return path.read_bytes()

    async def iter(self) -> AsyncIterator[FileInfo]:
        # 再帰列挙（rglob）。キーは self._dir からの相対 posix パスにし、'/' を含む
        # ネストキーも列挙する（s3/nats のフラットキー列挙と規約を揃える）。
        files = sorted(
            (f for f in self._dir.rglob("*") if f.is_file()),
            key=lambda p: p.relative_to(self._dir).as_posix(),
            reverse=True,
        )
        for f in files:
            yield FileInfo(filename=f.relative_to(self._dir).as_posix(), size=f.stat().st_size)

    async def list(self, limit: int = 10) -> list[FileInfo]:
        return await _take(self.iter(), limit)

    async def exists(self, key: str) -> bool:
        return (self._dir / key).is_file()

    async def delete(self, key: str) -> None:
        # ファイルだけ消す（空になった親ディレクトリは残す）。無いキーは無視。
        path = self._dir / key
        if path.is_file():
            path.unlink()

    async def vacuum(self) -> None:
        """空ディレクトリを再帰的に削除する（root 自身は残す）。delete とは別の保守操作。

        ローカルファイルシステム特有の掃除（s3/nats はフラットで空ディレクトリ概念が無い）。
        bottom-up に走査するので、ネストした空ディレクトリもまとめて畳む。
        """
        for dirpath, _dirnames, _filenames in os.walk(self._dir, topdown=False):
            p = Path(dirpath)
            if p != self._dir and not any(p.iterdir()):
                with contextlib.suppress(OSError):
                    p.rmdir()

    async def cp(self, src: str, dst: str) -> None:
        await _kv_copy(self, src, dst)  # get→put（put は原子的・親ディレクトリ作成）

    async def mv(self, src: str, dst: str) -> None:
        src_path = self._dir / src
        if not src_path.is_file():
            raise FileNotFoundError(src)
        dst_path = self._dir / dst
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src_path, dst_path)  # 同一 FS 内の原子的 rename

    async def connect(self) -> None:
        # ローカルは接続不要だが、ライフサイクルのステップを合わせるため dir を確実に用意する。
        self._dir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        return None


class LocalFileObject:
    """ローカルファイルハンドルを [FileObject] として被せる（IO 自体は同期）。"""

    def __init__(self, fh: BinaryIO) -> None:
        self._fh = fh

    async def read(self, size: int = -1) -> bytes:
        return self._fh.read(size)

    async def write(self, data: bytes) -> int:
        return self._fh.write(data)

    async def close(self) -> None:
        self._fh.close()

    async def __aenter__(self) -> LocalFileObject:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._fh.close()


class _LocalAtomicWriter:
    """一時ファイルへ書き、close（正常終了）でのみ `os.replace` で確定する書き込み [FileObject]。

    全部書けてから差し替えるので all-or-nothing（途中失敗・例外では確定せず一時ファイルを破棄）。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
        self._tmp = tmp
        self._fh = os.fdopen(fd, "wb")
        self._done = False

    async def read(self, size: int = -1) -> bytes:
        raise io.UnsupportedOperation("not readable")

    async def write(self, data: bytes) -> int:
        return self._fh.write(data)

    async def close(self) -> None:
        if self._done:
            return
        self._done = True
        self._fh.close()
        os.replace(self._tmp, self._path)  # ここで初めて確定（原子的差し替え）

    async def _abort(self) -> None:
        if self._done:
            return
        self._done = True
        self._fh.close()
        with contextlib.suppress(OSError):
            os.unlink(self._tmp)

    async def __aenter__(self) -> _LocalAtomicWriter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        if exc and exc[0] is not None:
            await self._abort()  # 例外時は確定しない
        else:
            await self.close()


class LocalFileStore:
    """`open` でファイルオブジェクトを返すローカル実装（[FileStore]）。書き込みは原子的。"""

    def __init__(self, directory: Path) -> None:
        # KVS と同様、初期化時に絶対パスへ固定する（実行中の cd で挙動を変えない）。
        self._dir = Path(directory).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)

    async def open(self, filename: str, mode: str = "rb") -> FileObject:
        path = self._dir / filename
        if "r" in mode:
            return LocalFileObject(path.open(mode))
        if "w" in mode:
            return _LocalAtomicWriter(path)  # temp+rename で all-or-nothing
        raise ValueError(f"unsupported mode for LocalFileStore: {mode!r}")

    async def connect(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        return None
