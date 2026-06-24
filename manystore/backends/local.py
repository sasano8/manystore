"""local backend — ローカルファイルシステム実装（KVS / FileStore）。

書き込みは temp+rename で原子的（all-or-nothing）。パスは init で絶対パスへ固定（cd 非依存）。

実装の真実は [LocalFileStore] に集約する（filesystem-native なので KVS の名前空間操作も
ここで担える）。[LocalKeyValueStore] は [KeyValueFromFileStore] を介した薄い KVS ビュー。
"""

import contextlib
import io
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from ..protocols import (
    AsyncFileObject,
    FileInfo,
    FileStoreBase,
    KeyValueFromFileStore,
    _kv_copy,
    scan_prefix,
)


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


class LocalFileStore(FileStoreBase):
    """ローカルファイルシステムの「真実の実装」（完全な [FileStore]＝KeyValueStore + IO）。

    **file 寄り**＝primitive は `open_reader`/`open_writer`（ストリーム）なので [FileStoreBase] を
    継承し、put/get/get_or_raise（全体）は基底が IO から導出する（値境界でのみバッファ）。本クラスは
    IO 2 つ＋名前空間操作（iter/list/exists/delete・cp/mv・vacuum）を filesystem-native に実装する
    ＝KeyValueStore も満たす。KVS ビュー（IO を隠したもの）は
    `KeyValueFromFileStore(LocalFileStore(...))`（＝[LocalKeyValueStore]）で被せる＝実装の二重持ちを
    避ける。書き込みは open_writer の temp+rename で原子的（all-or-nothing）。バイナリ専用。
    """

    def __init__(self, directory: Path) -> None:
        # 初期化時に絶対パスへ解決して固定する。実行中に cwd が cd で変わっても
        # 挙動が変わらないようにするため（相対パスのまま保持しない）。
        self._dir = Path(directory).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── ストリーム入出力（primitive）。put/get_or_raise/get は [FileStoreBase] が導出 ──

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return LocalFileObject((self._dir / filename).open("rb"))

    async def open_writer(self, filename: str) -> AsyncFileObject:
        # 親ディレクトリ作成＋temp+rename で all-or-nothing（ネストキーもそのまま置ける）。
        return _LocalAtomicWriter(self._dir / filename)

    # ── 名前空間操作（filesystem-native） ──

    async def iter_all(self, limit: int | None = None) -> AsyncIterator[FileInfo]:
        # 再帰列挙（rglob）。キーは self._dir からの相対 posix パスにし、'/' を含む
        # ネストキーも列挙する（s3/nats のフラットキー列挙と規約を揃える）。
        files = sorted(
            (f for f in self._dir.rglob("*") if f.is_file()),
            key=lambda p: p.relative_to(self._dir).as_posix(),
            reverse=True,
        )
        for f in files[:limit]:  # limit=None は全件（スライスがそのまま全要素）
            yield FileInfo(filename=f.relative_to(self._dir).as_posix(), size=f.stat().st_size)

    def iter_prefix(self, prefix: str) -> AsyncIterator[FileInfo]:
        # filesystem にサーバ側 prefix は無い＝scan で明示的に支える（暗黙 fallback ではない）。
        return scan_prefix(self, prefix)

    async def list_all(self, limit: int | None = None) -> list[FileInfo]:
        return [info async for info in self.iter_all(limit)]

    async def exists(self, filename: str) -> bool:
        return (self._dir / filename).is_file()

    async def delete(self, filename: str) -> None:
        # ファイルだけ消す（空になった親ディレクトリは残す）。無いキーは無視。
        path = self._dir / filename
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

    # ── ライフサイクル ──

    async def connect(self) -> None:
        # ローカルは接続不要だが、ライフサイクルのステップを合わせるため dir を確実に用意する。
        self._dir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        return None


class LocalKeyValueStore(KeyValueFromFileStore):
    """[LocalFileStore] を KVS ビューとして被せた薄いラッパ（実装は LocalFileStore に集約）。

    get/put は下層 open_reader/open_writer 越し、iter/list/exists/delete/cp/mv は素通し委譲
    （[KeyValueFromFileStore]）。vacuum だけは Local 固有（空ディレクトリ掃除・KVS Protocol 外）
    なのでここで足す。
    """

    def __init__(self, directory: Path) -> None:
        self._fs = LocalFileStore(directory)  # Local 固有操作（vacuum）用に concrete 参照を保持
        super().__init__(self._fs)

    async def vacuum(self) -> None:
        await self._fs.vacuum()
