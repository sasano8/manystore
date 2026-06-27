"""client 層のテスト（in-process ASGITransport で server と往復）。

RemoteKeyValueStore が [KeyValueStore] 準拠でサーバ越しに put/get/list/exists/delete/cp/mv できる。
pytest-asyncio（asyncio_mode=auto）で `async def test_*` をそのまま回す。
"""

from pathlib import Path

import httpx
import pytest

from manystore.client import RemoteKeyValueStore
from manystore.exceptions import ConflictError, NotFoundError
from manystore.protocols import AsyncKeyValueStore, FileInfo
from manystore.serving.server.app import create_app
from manystore.serving.server.routes import KV_RAW_PREFIX  # native NS prefix の単一正本
from manystore.serving.services.config import parse_config
from manystore.serving.services.service import StorageService
from manystore.tools.conformancer import (
    assert_concrete_store_signatures,
    assert_put_if_absent_concurrency_safe,
    assert_put_if_match_concurrency_safe,
    concrete_store_signature_errors,
)


async def _remote_store(tmp_path: Path) -> tuple[StorageService, RemoteKeyValueStore]:
    """local backend の server を in-process ASGITransport で繋いだ RemoteKeyValueStore を返す。"""
    cfg = parse_config({"contexts": {"work": {"backend": "local", "root": str(tmp_path)}}})
    service = StorageService(cfg, watch_interval=1.0)
    await service.connect()
    app = create_app(service)
    store = RemoteKeyValueStore(
        f"http://test{KV_RAW_PREFIX}", "work", transport=httpx.ASGITransport(app=app)
    )
    return service, store


def test_remote_kvs_signature_parity() -> None:
    """RemoteKeyValueStore が KeyValueStore Protocol を署名レベルで満たすこと（HTTP 越し前提）。

    挙動（roundtrip）の前に「remote が KeyValueStore の顔を被れているか」を機械検証する。
    存在＋パラメータ署名の一致を見る（`put` の `if_match` や `head`/`create` の drift を検出）。
    戻り注釈の narrowing（`iter_all` の AsyncIterable→AsyncIterator）は全 backend 共通の慣習ゆえ
    許容＝`concrete_store_signature_errors` の方針。strict な base↔Protocol parity は誤検出する。
    """
    assert concrete_store_signature_errors(RemoteKeyValueStore, AsyncKeyValueStore) == []
    assert_concrete_store_signatures(RemoteKeyValueStore, AsyncKeyValueStore)


async def test_remote_kvs_roundtrip(tmp_path: Path) -> None:
    cfg = parse_config(
        {"contexts": {"work": {"backend": "local", "root": str(tmp_path)}}},
    )
    service = StorageService(cfg, watch_interval=1.0)

    # lifespan を介さず手動接続（ASGITransport は lifespan を起動しないため）。
    await service.connect()
    app = create_app(service)
    transport = httpx.ASGITransport(app=app)
    # base_url = host + native NS prefix（router アタッチ先と同じ定数で組む＝ベタ書きしない）。
    store = RemoteKeyValueStore(f"http://test{KV_RAW_PREFIX}", "work", transport=transport)
    try:
        assert await store.get("a.txt") is None
        await store.put("a.txt", b"hello")
        assert await store.get("a.txt") == b"hello"
        assert await store.exists("a.txt") is True

        await store.put("b.txt", b"world")
        keys = {i["filename"] for i in await store.list_all(10)}
        assert keys == {"a.txt", "b.txt"}

        await store.cp("a.txt", "c.txt")
        assert await store.get("c.txt") == b"hello"
        await store.mv("c.txt", "d.txt")
        assert await store.get("c.txt") is None
        assert await store.get("d.txt") == b"hello"

        await store.delete("a.txt")
        assert await store.get("a.txt") is None
    finally:
        await store.aclose()
        await service.aclose()


async def test_remote_get_or_raise_and_default(tmp_path: Path) -> None:
    # get_or_raise が client/service に波及済み：欠損は NotFoundError、get は default を返す。
    cfg = parse_config({"contexts": {"work": {"backend": "local", "root": str(tmp_path)}}})
    service = StorageService(cfg, watch_interval=1.0)
    await service.connect()
    app = create_app(service)
    store = RemoteKeyValueStore(
        f"http://test{KV_RAW_PREFIX}", "work", transport=httpx.ASGITransport(app=app)
    )
    try:
        # サーバ層（StorageService）の get_or_raise も欠損で NotFoundError。
        with pytest.raises(NotFoundError):
            await service.get_or_raise("work", "missing.txt")

        # クライアント層（RemoteKeyValueStore）：欠損は get_or_raise が送出、get は default。
        with pytest.raises(NotFoundError):
            await store.get_or_raise("missing.txt")
        assert await store.get("missing.txt", default=b"fallback") == b"fallback"

        await store.put("k.txt", b"v")
        assert await store.get_or_raise("k.txt") == b"v"
    finally:
        await store.aclose()
        await service.aclose()


# ── conditional put（CAS）を HTTP 越しに検証（M046 案B「HTTP 越し conformance」step2/3） ──


async def test_remote_head_exposes_version(tmp_path: Path) -> None:
    # HEAD が version トークン（etag）を露出し、remote.head が CAS に使える FileInfo を返す。
    service, store = await _remote_store(tmp_path)
    try:
        with pytest.raises(NotFoundError):  # 欠損は NotFoundError（head 規約）
            await store.head("missing")
        absent = await store.head_or_absent("missing")
        assert absent.is_absent()  # 欠損は不在 FileInfo

        await store.put("k", b"hello")
        info = await store.head("k")
        assert info["size"] == 5 and info.get("etag") is not None  # native トークンが乗る
        assert not info.is_absent()
    finally:
        await store.aclose()
        await service.aclose()


async def test_remote_conditional_put_single_shot(tmp_path: Path) -> None:
    # 単発の条件 put: create-only の二度目は Conflict、update CAS は古い版で Conflict・新版で成功。
    service, store = await _remote_store(tmp_path)
    try:
        # create-only（不在を要求）: 1 回目成功・2 回目は既存ゆえ ConflictError。
        await store.put("c", b"v1", if_match=FileInfo.absent("c"))
        with pytest.raises(ConflictError):
            await store.put("c", b"v2", if_match=FileInfo.absent("c"))
        assert await store.get_or_raise("c") == b"v1"  # 敗者に上書きされない

        # update CAS: head の版で更新成功 → 版が進む。古い版での再更新は ConflictError。
        v1 = await store.head("c")
        await store.put("c", b"v2", if_match=v1)
        assert await store.get_or_raise("c") == b"v2"
        with pytest.raises(ConflictError):
            await store.put("c", b"v3", if_match=v1)  # 版が進んでいる＝lost-update を拒否
    finally:
        await store.aclose()
        await service.aclose()


async def test_remote_conformance_create_only_concurrency(tmp_path: Path) -> None:
    # conformancer の create-only 並行安全性チェッカを **HTTP 越し**に回す（client→server→local）。
    service, store = await _remote_store(tmp_path)
    try:
        await assert_put_if_absent_concurrency_safe(store, size=4096)
    finally:
        await store.aclose()
        await service.aclose()


async def test_remote_conformance_update_cas_concurrency(tmp_path: Path) -> None:
    # conformancer の update CAS 並行安全性チェッカを **HTTP 越し**に回す（lost-update を拒否）。
    service, store = await _remote_store(tmp_path)
    try:
        await assert_put_if_match_concurrency_safe(store, size=4096)
    finally:
        await store.aclose()
        await service.aclose()
