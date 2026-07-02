"""client 層のテスト（in-process ASGITransport で server と往復）。

RemoteKeyValueStore が [KeyValueStore] 準拠でサーバ越しに put/get/list/exists/delete/cp/mv できる。
pytest-asyncio（asyncio_mode=auto）で `async def test_*` をそのまま回す。
"""

from pathlib import Path

import httpx
import pytest

from manystore.client import ManystoreClient, RemoteKeyValueStore
from manystore.serving.server.app import create_app
from manystore.serving.server.routes import KV_RAW_PREFIX  # native NS prefix の単一正本
from manystore.serving.services.config import parse_config
from manystore.serving.services.service import StorageService
from manystore.spec import AsyncBufferedStore, FileInfo
from manystore.spec.exceptions import ConflictError, NotFoundError
from manystore.tools.conformancer import (
    assert_concrete_store_signatures,
    assert_fail_loud_over_transport,
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
    assert concrete_store_signature_errors(RemoteKeyValueStore, AsyncBufferedStore) == []
    assert_concrete_store_signatures(RemoteKeyValueStore, AsyncBufferedStore)


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


# Remote 越しの挙動契約（run_light/run_middle/writer/並行 CAS）は集約ハーネス
# （test_conformance_matrix）の "remote" provider に移設＝Dict/Local/実 backend と 1 か所で検証。
# ここには client 固有の挙動（署名 parity・roundtrip・head メタ・exists fail-loud）だけ残す。


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, True), (404, False)],
)
async def test_remote_exists_status_mapping(status: int, expected: bool) -> None:
    """M055: exists は 200→True / 404→False。HEAD のステータスを正しく写す。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        return httpx.Response(status)

    client = ManystoreClient("http://test/kv/raw", transport=httpx.MockTransport(handler))
    try:
        assert await client.exists("work", "k") is expected
    finally:
        await client.aclose()


async def test_remote_exists_is_fail_loud_on_server_error() -> None:
    """M055: exists は 5xx 等の障害を False（＝「無い」）に握り潰さず loud に伝播する（要求7）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = ManystoreClient("http://test/kv/raw", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.exists("work", "k")
    finally:
        await client.aclose()


async def test_remote_is_fail_loud_over_transport_fault() -> None:
    """M065 step5 / M066 step3: server/backend 障害（500）を client が**全 op で**握り潰さず raise。

    server 越し（transport-level fault）の fail-loud を契約化＝conformancer の
    `assert_fail_loud_over_transport` を 500 を返す transport の `RemoteKeyValueStore` に当てる。
    get/get(default)/exists/delete/put/list/iter のどれも障害を None/False/default/NotFound に
    化けさせず loud に失敗（M054〔欠損偽装〕/M055〔False 偽装〕のクラスを HTTP 越しで横断検知）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "injected backend fault"})

    store = RemoteKeyValueStore(
        "http://test/kv/raw", "work", transport=httpx.MockTransport(handler)
    )
    try:
        await assert_fail_loud_over_transport(store)
    finally:
        await store.aclose()
