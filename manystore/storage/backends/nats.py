"""nats backend — NATS JetStream Object Store（KVS / FileStore）。

nats-py はメソッド内で遅延 import する。FileStore は read=全体取得 / write=close で put。

**conditional put（CAS・M046）**: NATS Object Store の高レベル `obs.put` は OCC を露出しないため、
version トークン＝**オブジェクトのメタ subject（`$O.<bucket>.M.<b64(name)>`）の最終ストリーム
シーケンス**を採り、メタ publish に JetStream の `Nats-Expected-Last-Subject-Sequence` を付けて
**サーバ側で原子的に**条件判定する（不一致は err_code 10071＝`ConflictError`）。create-only は
baseline seq（不在=0／tombstone=その seq）、update CAS は etag(=seq) を期待値にする。
これは object store の内部ワイヤ形式（チャンク subject／メタ JSON／ROLLUP）に依存＝nats-py の
仕様変更に弱いが object store で原子 CAS を得る唯一の経路（既存の get_info/digest 参照も同様）。
"""

import asyncio
import contextlib
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
)

# JetStream が「期待した最終 subject シーケンスと不一致」を返すときの err_code（CAS 失敗の判別）。
_WRONG_LAST_SEQ_ERR = 10071


class _NatsBase:
    """NATS object store の共通接続部（lazy connect の `_get_obs`）。"""

    def __init__(self, url: str, bucket: str) -> None:
        self._url = url
        self._bucket = bucket
        self._nc = None
        self._obs = None
        self._connect_lock = (
            asyncio.Lock()
        )  # lazy connect を直列化（並行 _get_obs の二重接続を防ぐ）

    async def _get_obs(self):
        # ロック無しだと並行で nc が 2 本張られ片方が捨てられリーク（aclose は 1 本しか閉じない）。
        # 速い道（確立済み）はロックを取らず、未確立時だけロック内で double-checked init する。
        if self._obs is not None:
            return self._obs
        async with self._connect_lock:
            if self._obs is None:  # ロック待ちの間に他コルーチンが確立した可能性を再確認
                import nats
                from nats.js.errors import BucketNotFoundError

                nc = await nats.connect(self._url)
                js = nc.jetstream()
                try:
                    obs = await js.object_store(self._bucket)
                except BucketNotFoundError:
                    obs = await js.create_object_store(self._bucket)
                self._nc = nc
                self._obs = obs  # 確立し切ってから公開（部分状態を速い道に見せない）
        return self._obs

    async def connect(self) -> None:
        # nc 接続＋object store を確立する（以降は使い回す）。
        await self._get_obs()

    async def aclose(self) -> None:
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
            self._obs = None


class NatsObjectKeyValueStore(_NatsBase, KeyValueStoreBase):
    def _meta_subject(self, key: str) -> str:
        """オブジェクトのメタ subject（`$O.<bucket>.M.<b64url(name)>`）。CAS の version 担体。"""
        import base64

        from nats.js.object_store import OBJ_META_PRE_TEMPLATE

        return OBJ_META_PRE_TEMPLATE.format(
            bucket=self._bucket, obj=base64.urlsafe_b64encode(key.encode()).decode()
        )

    async def _seq_and_info(self, key: str) -> tuple[int | None, dict | None]:
        """メタ subject の最終 (seq, info dict)。メッセージが無ければ (None, None)。

        seq が version トークン（etag）。info はメタ JSON（`deleted`/`nuid`/`size` を読む）。
        """
        import json

        from nats.js.errors import NotFoundError as JSNotFound
        from nats.js.object_store import OBJ_STREAM_TEMPLATE

        obs = await self._get_obs()  # 接続を確実にする（stream 名は bucket から定形）
        stream = OBJ_STREAM_TEMPLATE.format(bucket=self._bucket)
        try:
            msg = await obs._js.get_last_msg(stream, self._meta_subject(key))
        except JSNotFound:
            # 単一クラス catch（`except (A, B):` の tuple 形は作業環境が py2 構文へ書き戻す既知の
            # 異常があるため避ける）。空 subject で NotFoundError＝未作成（tombstone も無い）。
            return None, None
        return msg.seq, json.loads(msg.data)

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        # if_match 省略＝無条件 LWW（object store の put は単一オブジェクト原子）。
        if if_match is None:
            obs = await self._get_obs()
            await obs.put(key, value)
            return FileInfo(filename=key, size=len(value))

        # conditional＝メタ subject の最終 seq を baseline に期待値を決める（不一致は Conflict）。
        seq, info = await self._seq_and_info(key)
        if if_match.is_absent():
            # create-only: 既存（非削除）なら衝突。不在=0／tombstone=その seq を baseline に。
            if seq is not None and info is not None and not info.get("deleted"):
                raise ConflictError(f"key already exists: {key}")
            expected = 0 if seq is None else seq
        else:
            # update CAS: etag(=seq) 一致を要求（不在・削除済み・版ズレは衝突）。
            expected = int(if_match.get("etag") or -1)
            if seq is None or info is None or info.get("deleted") or seq != expected:
                raise ConflictError(f"version mismatch: {key}")
        old_nuid = info.get("nuid") if info else None
        await self._put_with_occ(key, value, expected, old_nuid)
        return FileInfo(filename=key, size=len(value))

    async def _put_with_occ(
        self, key: str, value: bytes, expected: int, old_nuid: str | None
    ) -> None:
        """チャンク書き込み＋メタ publish を `Nats-Expected-Last-Subject-Sequence` 付きで原子実行。

        期待 seq と不一致なら JetStream が err_code 10071＝`ConflictError`（lost-update を拒否）。
        値は bytes（manystore は値＝bytes）なので obs.put の streaming は使わず最小に再実装する。
        """
        import base64
        import json
        from datetime import UTC, datetime
        from hashlib import sha256

        from nats.js import api
        from nats.js.errors import APIError
        from nats.js.kv import MSG_ROLLUP_SUBJECT
        from nats.js.object_store import (
            OBJ_CHUNKS_PRE_TEMPLATE,
            OBJ_DEFAULT_CHUNK_SIZE,
            OBJ_DIGEST_TEMPLATE,
            OBJ_STREAM_TEMPLATE,
        )

        obs = await self._get_obs()
        js = obs._js
        stream = OBJ_STREAM_TEMPLATE.format(bucket=self._bucket)
        # 名前再利用でもチャンクが新 subject へ載るよう新しい nuid を振る（obs.put と同じ）。
        nuid = self._nc._nuid.next().decode()
        chunk_subj = OBJ_CHUNKS_PRE_TEMPLATE.format(bucket=self._bucket, obj=nuid)

        h = sha256()
        sent = 0
        total = 0
        mv = memoryview(value)
        for off in range(0, len(value), OBJ_DEFAULT_CHUNK_SIZE):
            payload = bytes(mv[off : off + OBJ_DEFAULT_CHUNK_SIZE])
            h.update(payload)
            await js.publish(chunk_subj, payload)
            sent += 1
            total += len(payload)
        digest = OBJ_DIGEST_TEMPLATE.format(digest=base64.urlsafe_b64encode(h.digest()).decode())
        meta = api.ObjectInfo(
            name=key,
            bucket=self._bucket,
            nuid=nuid,
            size=total,
            chunks=sent,
            digest=digest,
            mtime=datetime.now(UTC).isoformat(),
            options=api.ObjectMetaOptions(max_chunk_size=OBJ_DEFAULT_CHUNK_SIZE),
        )
        try:
            await js.publish(
                self._meta_subject(key),
                json.dumps(meta.as_dict()).encode(),
                headers={
                    api.Header.ROLLUP.value: MSG_ROLLUP_SUBJECT,
                    api.Header.EXPECTED_LAST_SUBJECT_SEQUENCE.value: str(expected),
                },
            )
        except APIError as e:
            await js.purge_stream(stream, subject=chunk_subj)  # 書いたチャンクを巻き戻す
            if getattr(e, "err_code", None) == _WRONG_LAST_SEQ_ERR:
                raise ConflictError(f"version conflict: {key}") from e
            raise
        if old_nuid and old_nuid != nuid:  # 旧版のチャンクを掃除（obs.put と同じ後始末）
            await js.purge_stream(
                stream, subject=OBJ_CHUNKS_PRE_TEMPLATE.format(bucket=self._bucket, obj=old_nuid)
            )

    async def head(self, key: str) -> FileInfo:
        # version＝メタ subject の最終 seq（CAS の期待値に使う不透明トークン）。欠損/削除は NotFound
        # （mtime は版差ゆえ modified_at=None）。
        seq, info = await self._seq_and_info(key)
        if seq is None or info is None or info.get("deleted"):
            raise NotFoundError(key)
        return FileInfo(filename=key, size=info.get("size") or 0, modified_at=None, etag=str(seq))

    async def get_or_raise(self, key: str) -> bytes:
        from nats.js.errors import NotFoundError as JSNotFound

        obs = await self._get_obs()
        try:
            result = await obs.get(key)
        except JSNotFound as e:
            # 欠損のみ正規化。障害（接続断/認証/timeout）は伝播＝fail-loud（要求7）。
            # 単一クラス catch（tuple 形 `except (A, B):` は py2 書き戻し既知異常を避けるため）。
            raise NotFoundError(key) from e
        return result.data or b""

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        from nats.js.errors import NotFoundError

        obs = await self._get_obs()
        try:
            entries = await obs.list()
        except NotFoundError:
            # TODO(M041): not-found catch を obs.watch() ベース再実装で撤去
            # 空ストアは list() が NotFoundError＝空扱い。接続断・認証等の本物のエラーは
            # 握り潰さず伝播させる（fail-loud。空と障害を取り違えない）。
            entries = []
        entries = [e for e in entries if not e.deleted]
        entries.sort(key=lambda e: e.name, reverse=True)
        # NATS にサーバ側 prefix は無い＝scan+filter で支える。prefix で絞ってから limit を適用。
        count = 0
        for e in entries:
            if prefix and not e.name.startswith(prefix):
                continue
            if limit is not None and count >= limit:
                return
            yield FileInfo(filename=e.name, size=e.size or 0)
            count += 1

    async def exists(self, key: str) -> bool:
        from nats.js.errors import NotFoundError

        obs = await self._get_obs()
        try:
            info = await obs.get_info(key)  # ObjectStore に info は無い。get_info が正
        except NotFoundError:
            # TODO(M041): not-found catch を obs.watch() ベース再実装で撤去
            # 欠損/削除済み（ObjectNotFoundError/ObjectDeletedError は NotFoundError 派生）のみ
            # False。接続断・認証等の本物のエラーは握り潰さず伝播（fail-loud）。
            return False
        return not info.deleted

    async def delete(self, key: str) -> None:
        from nats.js.errors import NotFoundError as JSNotFound

        obs = await self._get_obs()
        # 欠損/削除済み（ObjectNotFoundError/ObjectDeletedError は JSNotFound 派生）のみ冪等 no-op。
        # 接続断・認証・timeout 等の本物の障害は握り潰さず伝播させる（fail-loud・要求7）。
        # 単一クラス catch（tuple 形 `except (A, B):` は py2 書き戻し既知異常を避けるため）。
        with contextlib.suppress(JSNotFound):
            await obs.delete(key)

    async def cp(self, src: str, dst: str) -> None:
        await _kv_copy(self, src, dst)

    async def mv(self, src: str, dst: str) -> None:
        await _kv_move(self, src, dst)


# ── FileStore（= KVS ＋ buffer 合成 IO） ──


class NatsFileStore(NatsObjectKeyValueStore):
    """NATS の完全な [FileStore]（= [NatsObjectKeyValueStore] ＋ buffer 合成 IO）。

    NATS Object Store は **kv 寄り**＝whole get/put が native（核は KVS 側）。真の bounded
    ストリーミングは `get(writeinto=...)` の逐次配送が nats-py 仕様で executor スレッドから呼ばれ、
    スレッド安全な受け渡しが要るため未採用＝deferred。よって open_reader/open_writer は **whole
    get/put の上に buffer で被せた擬似ストリーム**（共有の [_KvReadFileObject]/[_KvWriteFileObject]
    を流用）。KVS 面は継承。
    """

    async def open_reader(self, filename: str) -> AsyncFileObject:
        return _KvReadFileObject(await self.get_or_raise(filename))  # whole get を buffer 化

    async def open_writer(self, filename: str) -> AsyncFileObject:
        return _KvWriteFileObject(self, filename)  # close で whole put
