"""conformance — ストア実装が抽象 Protocol に準拠するかを検査する再利用ツール。

サードパーティが新しい backend を実装したとき、`pytest` などから簡単に「前提とする Protocol に
準拠しているか」を横断的に確認できるようにするツール。2 段階で確認できる:

1. **メソッド存在チェック**（`assert_key_value_store` / `assert_file_store`）— Protocol メンバが
   callable な属性として在るか。`typing.get_protocol_members`（継承を含む）が対象。
2. **挙動契約チェック**（`check_key_value_store_contract` / `check_file_store_contract`）— 実際に
   put/get/get_or_raise/exists/list/iter/cp/mv・open_reader/open_writer を叩いて backend 非依存の
   振る舞い契約を満たすか。read-only backend は `writable=False`（書き込み拒否のみ確認）。

**シグネチャ検査は未実装**（必要になってから足す）。

使い方（サードパーティ backend のテスト例）::

    import asyncio
    from manystore.conformance import assert_key_value_store, check_key_value_store_contract

    def test_my_backend_conforms():
        assert_key_value_store(MyKeyValueStore())              # メソッドが揃っているか
        async def run():
            async with open_my_store() as store:               # 接続済みの空ストアを渡す
                await check_key_value_store_contract(store)     # 挙動契約を満たすか
        asyncio.run(run())
"""

import contextlib
import io
import typing
import uuid

from .async_storage import FileStore, KeyValueStore


def required_members(protocol: type) -> frozenset[str]:
    """`protocol` が要求するメンバ名の集合（継承した Protocol のメンバも含む）。"""
    return typing.get_protocol_members(protocol)


def missing_members(obj: object, protocol: type) -> set[str]:
    """`obj` に欠けている、または callable でない `protocol` メンバ名の集合。"""
    return {name for name in required_members(protocol) if not callable(getattr(obj, name, None))}


def assert_implements(obj: object, protocol: type) -> None:
    """`obj` が `protocol` の全メソッドを（callable な属性として）持つことを表明する。

    欠けていれば `AssertionError`（不足メンバ名を列挙）。挙動・シグネチャは検査しない。
    """
    missing = missing_members(obj, protocol)
    if missing:
        raise AssertionError(
            f"{type(obj).__name__} は {protocol.__name__} の "
            f"{sorted(missing)} を実装していません（メソッド存在チェック）"
        )


def assert_key_value_store(obj: object) -> None:
    """`obj` が [KeyValueStore] の全メソッドを持つことを表明する。"""
    assert_implements(obj, KeyValueStore)


def assert_file_store(obj: object) -> None:
    """`obj` が [FileStore]（= KeyValueStore + open_reader/open_writer）を持つことを表明する。"""
    assert_implements(obj, FileStore)


# ── 挙動契約チェック（実際に叩いて backend 非依存の振る舞いを検証） ──


async def check_key_value_store_contract(store: object, *, writable: bool = True) -> None:
    """`store`（接続済み）が [KeyValueStore] の**挙動契約**を満たすか実際に叩いて検証する。

    生成キーは末尾で best-effort 削除。list/iter は共有 backend を考慮し**部分集合**（キーが現れる
    か）で確認する。read-only backend は `writable=False`＝put/delete/cp/mv が
    `io.UnsupportedOperation` を投げることだけ確認。契約違反は `AssertionError`。
    """
    base = f"_conformance/{uuid.uuid4().hex}"
    k = f"{base}/a"

    if not writable:
        for op, coro in (
            ("put", store.put(k, b"x")),
            ("delete", store.delete(k)),
            ("cp", store.cp(k, k + "2")),
            ("mv", store.mv(k, k + "2")),
        ):
            try:
                await coro
            except io.UnsupportedOperation:
                continue
            raise AssertionError(f"read-only store の {op} が io.UnsupportedOperation を投げない")
        return

    created: list[str] = []
    try:
        # 1. 欠損キーのセマンティクス
        assert await store.get(k) is None, "欠損 get は None を返す"
        assert await store.get(k, b"def") == b"def", "欠損 get は default を返す"
        assert await store.exists(k) is False, "欠損 exists は False"
        try:
            await store.get_or_raise(k)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("欠損 get_or_raise は FileNotFoundError を投げる")

        # 2. put→get ラウンドトリップ（バイナリ安全・'/' を含むネストキー）
        payload = b"hello\x00\xffworld"
        await store.put(k, payload)
        created.append(k)
        assert await store.get(k) == payload, "put した値が get で一致する"
        assert await store.get_or_raise(k) == payload, "get_or_raise も一致する"
        assert await store.exists(k) is True, "put 後 exists は True"

        # 3. 上書き
        await store.put(k, b"v2")
        assert await store.get(k) == b"v2", "同一キーへの put は上書き"

        # 4. list / iter にキーが現れる（順序・全体一致は要求しない＝部分集合）
        listed = {info["filename"] for info in await store.list(limit=1000)}
        assert k in listed, "list にキーが現れる"
        iterated = set()
        async for info in store.iter():
            assert {"filename", "size"} <= set(info.keys()), "FileInfo は filename/size を持つ"
            iterated.add(info["filename"])
        assert k in iterated, "iter にキーが現れる"

        # 5. cp（コピー先に値・src は残存）
        dst = f"{base}/b"
        await store.cp(k, dst)
        created.append(dst)
        assert await store.get(dst) == b"v2", "cp 先に値が入る"
        assert await store.exists(k) is True, "cp 後も src は残存"

        # 6. mv（移動先に値・src は消失）
        moved = f"{base}/c"
        await store.mv(dst, moved)
        created.append(moved)
        created.remove(dst)
        assert await store.get(moved) == b"v2", "mv 先に値が入る"
        assert await store.exists(dst) is False, "mv 後 src は消失"

        # 7. delete（冪等）
        await store.delete(k)
        created.remove(k)
        assert await store.exists(k) is False, "delete 後 exists は False"
        assert await store.get(k) is None, "delete 後 get は None"
        await store.delete(k)  # 無いキーの delete は例外を投げない（冪等）
    finally:
        for key in created:
            with contextlib.suppress(Exception):
                await store.delete(key)


async def check_file_store_contract(store: object, *, writable: bool = True) -> None:
    """`store` が [FileStore]（= KVS + IO）の挙動契約を満たすか検証する。

    KVS 契約に加え、open_writer→open_reader のラウンドトリップ（全体/部分 read）と、欠損
    open_reader が `FileNotFoundError` を投げることを確認。read-only は open_writer の拒否のみ。
    """
    await check_key_value_store_contract(store, writable=writable)

    if not writable:
        try:
            await store.open_writer(f"_conformance/{uuid.uuid4().hex}")
        except io.UnsupportedOperation:
            return
        raise AssertionError(
            "read-only FileStore の open_writer が io.UnsupportedOperation を投げない"
        )

    key = f"_conformance/{uuid.uuid4().hex}/f"
    try:
        async with await store.open_writer(key) as w:
            await w.write(b"hello ")
            await w.write(b"world")  # 複数 write は close でまとまる
        async with await store.open_reader(key) as r:
            assert await r.read() == b"hello world", "open_reader の全体 read が一致"
        async with await store.open_reader(key) as r:
            assert await r.read(5) == b"hello", "open_reader の部分 read が一致"
        try:
            await store.open_reader(f"_conformance/{uuid.uuid4().hex}/missing")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("欠損 open_reader は FileNotFoundError を投げる")
    finally:
        with contextlib.suppress(Exception):
            await store.delete(key)
