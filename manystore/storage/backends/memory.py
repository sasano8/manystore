"""memory backend — プロセス内の辞書（dict）ストア（KVS / FileStore）。

依存ゼロ・接続不要のインメモリ実装。テストの参照 backend や、軽量な一時ストアに使う
（プロセス終了で揮発）。dict は **kv 寄り**（whole get/put が native）なので、核は KVS 側に置き、
FileStore は KVS を継承して IO を buffer 合成する（systemPatterns 原則7）。
"""

import time
from collections.abc import AsyncIterator

from ...exceptions import ConflictError, NotFoundError
from ...protocols import (
    AsyncFileObject,
    FileInfo,
    IfMatch,
    KeyValueStoreBase,
    _kv_copy,
    _kv_move,
    _KvReadFileObject,
    _KvWriteFileObject,
    _sha256_hex,
)


class DictKeyValueStore(KeyValueStoreBase):
    """`dict[str, bytes]` を保持するインメモリ [KeyValueStore]。

    外から既存 dict を渡せば共有・観測できる（テストの fake 兼参照実装）。iter は他 backend と
    同じく名前降順。get の primitive は get_or_raise（欠損で FileNotFoundError）で、get(default) は
    基底 [KeyValueStoreBase] が与える。

    conditional put（CAS）用に **メタストア**（`_etag`/`_mtime`）を値と同時更新する。素の dict には
    更新時刻・版が無いため、put のたびに **単調増加の通し番号**（`_seq`）を etag に焼き、`_mtime` に
    実クロックを記録する。通し番号はグローバル単調＝delete→再作成で版が再利用されない（ABA 安全）。
    外部から直接挿入されたキーはメタが無く、head は etag/modified_at=None（CAS は不一致＝安全側）。
    """

    def __init__(self, data: dict[str, bytes] | None = None) -> None:
        self._data: dict[str, bytes] = data if data is not None else {}
        self._etag: dict[str, str] = {}  # key -> 不透明な版トークン（通し番号の文字列）
        self._mtime: dict[str, float] = {}  # key -> 更新時刻（epoch 秒）
        self._sha256: dict[str, str] = {}  # key -> 内容 sha256（hex・M013／download 検証メタ）
        self._seq = 0  # put ごとに単調増加（版トークンの源・ABA 回避）

    def _bump_meta(self, key: str) -> None:
        self._seq += 1
        self._etag[key] = str(self._seq)
        self._mtime[key] = time.time()

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        value = bytes(value)
        exists = key in self._data
        # 条件判定→set の間に await を挟まない＝単一イベントループ内では原子的（割り込まれない）。
        if if_match is not None and if_match.is_absent() and exists:
            raise ConflictError(f"key already exists: {key}")  # create-only＝既存なら衝突
        # update CAS: 期待 etag と現在 etag を突合（不在・不一致は Conflict＝lost-update 検出）。
        is_update_cas = if_match is not None and not if_match.is_absent()
        if is_update_cas and (not exists or if_match.get("etag") != self._etag.get(key)):
            raise ConflictError(f"version mismatch: {key}")
        self._data[key] = value
        self._sha256[key] = _sha256_hex(value)  # 内容ハッシュも同時更新（head で露出・M013）
        self._bump_meta(key)
        return FileInfo(filename=key, size=len(value))

    async def head(self, key: str) -> FileInfo:
        if key not in self._data:
            raise NotFoundError(key)
        return FileInfo(
            filename=key,
            size=len(self._data[key]),
            modified_at=self._mtime.get(key),
            etag=self._etag.get(key),
            sha256=self._sha256.get(key),  # 外部直挿入キーは None（メタ無し）
        )

    async def get_or_raise(self, key: str) -> bytes:
        try:
            return self._data[key]
        except KeyError as e:
            raise NotFoundError(key) from e  # 欠損は NotFoundError に正規化

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # 名前降順（他 backend と整合）。dict にサーバ側 prefix は無い＝scan+filter で支える。
        # prefix で絞ってから limit を適用する（limit は「絞り込み後」の件数上限）。
        count = 0
        for key in sorted(self._data, reverse=True):
            if prefix and not key.startswith(prefix):
                continue
            if limit is not None and count >= limit:
                return
            yield FileInfo(filename=key, size=len(self._data[key]))
            count += 1

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)  # 無いキーは無視
        self._etag.pop(key, None)  # メタも掃除（再作成は新しい通し番号で版を振り直す）
        self._mtime.pop(key, None)
        self._sha256.pop(key, None)

    async def cp(self, src: str, dst: str) -> None:
        await _kv_copy(self, src, dst)

    async def mv(self, src: str, dst: str) -> None:
        await _kv_move(self, src, dst)

    async def connect(self) -> None:
        return None  # インメモリ＝接続不要

    async def aclose(self) -> None:
        return None


class DictFileStore(DictKeyValueStore):
    """`dict` を保持する完全な [FileStore]（= [DictKeyValueStore] ＋ buffer 合成 IO）。

    dict は kv 寄りなので KVS 面を継承し、open_reader/open_writer は whole get/put の上に
    buffer を被せた擬似ストリーム（共有 [_KvReadFileObject]/[_KvWriteFileObject] を流用）。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return _KvReadFileObject(await self.get_or_raise(filename))  # 欠損は FileNotFoundError

    async def open_writer(self, filename: str) -> AsyncFileObject:
        return _KvWriteFileObject(self, filename)  # close で whole put
