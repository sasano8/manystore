"""挙動契約を流す FileStore プロバイダの**単一宣言**（Dict / Local / Remote / 実 backend）。

`KeyValueStore` / `FileStore` の IF が揃うので、同一の契約 body（run_light / run_middle /
writer all-or-nothing / 並行 CAS / CRUD）を**注入するストアだけ変えて**全実装で回せる。ここに
backend を 1 か所だけ宣言し、`test_conformance_matrix` が各契約を全 provider に流す。

各 provider は **接続済み FileStore** を yield する async CM。KVS-native な backend（nats/s3/
remote）は `KeyValueFileStore` で FileStore 化して被せる（put/get/iter/exists/delete と
put(if_match)/head を下層へ委譲＝同じ契約で検査できる）。

フラグ:
- `gated` … 実 backend（未到達なら skip・`slow` マーク・環境/認証未整備の実行時エラーも skip）。

run_light/run_middle は **非破壊**（uuid 名前空間に閉じて操作し後始末する・M066①）になったので、
実共有 backend（nats/s3）にもそのまま流せる＝`isolated` 区別は不要になり廃止した。
"""

import contextlib
import os
import socket
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from manystore import (
    ConnectPolicy,
    DictFileStore,
    KeyValueFileStore,
    LocalFileStore,
    connect_key_value_store,
)
from manystore.client import RemoteKeyValueStore
from manystore.serving.server.app import create_app
from manystore.serving.server.routes import KV_RAW_PREFIX
from manystore.serving.services.config import parse_config
from manystore.serving.services.service import StorageService
from manystore.tools.conformancer import InjectedFault

# ── 実 backend の接続情報（`make e2e-up` の dev 既定。別サーバは env で上書き）。 ──
S3_HOST, S3_PORT = "localhost", 8333
NATS_HOST, NATS_PORT = "localhost", 4222
S3_ENDPOINT = f"http://{S3_HOST}:{S3_PORT}"
NATS_URL = f"nats://{NATS_HOST}:{NATS_PORT}"
S3_BUCKET = "manystore-e2e"
S3_ACCESS_KEY = os.environ.get("MANYSTORE_S3_ACCESS_KEY", "manystore")
S3_SECRET_KEY = os.environ.get("MANYSTORE_S3_SECRET_KEY", "manystoresecret123")


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _s3_up() -> bool:
    return _reachable(S3_HOST, S3_PORT)


def _s3_virtual_up() -> bool:
    # virtual-host は `<bucket>.<host>` を解決できる DNS 環境のみ成立（ローカルは即 skip・R13）。
    return bool(os.environ.get("MANYSTORE_S3_VIRTUAL")) and _s3_up()


def _nats_up() -> bool:
    return _reachable(NATS_HOST, NATS_PORT)


# ── provider（接続済み FileStore を yield する async CM）の宣言 ──


@asynccontextmanager
async def _open_dict() -> AsyncIterator[object]:
    yield DictFileStore()


@asynccontextmanager
async def _open_local() -> AsyncIterator[object]:
    with tempfile.TemporaryDirectory() as d:
        yield LocalFileStore(Path(d))


@asynccontextmanager
async def _open_remote() -> AsyncIterator[object]:
    # client → server → local(tmp) を in-process ASGI で繋ぐ（外部不要・揮発＝isolated）。
    with tempfile.TemporaryDirectory() as d:
        cfg = parse_config({"contexts": {"work": {"backend": "local", "root": d}}})
        service = StorageService(cfg, watch_interval=1.0)
        await service.connect()
        app = create_app(service)
        remote = RemoteKeyValueStore(
            f"http://test{KV_RAW_PREFIX}", "work", transport=httpx.ASGITransport(app=app)
        )
        try:
            yield KeyValueFileStore(remote)  # KVS を FileStore 化（IO も HTTP 往復で回る）
        finally:
            await remote.aclose()
            await service.aclose()


@asynccontextmanager
async def _open_nats() -> AsyncIterator[object]:
    async with connect_key_value_store(
        "nats", nats_url=NATS_URL, nats_bucket="manystore_e2e", policy=ConnectPolicy.fail_fast()
    ) as store:
        yield KeyValueFileStore(store)


async def _s3_ensure_bucket(addressing_style: str) -> None:
    from aiobotocore.config import AioConfig
    from aiobotocore.session import get_session

    session = get_session()
    async with session.create_client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=AioConfig(s3={"addressing_style": addressing_style}),
    ) as client:
        with contextlib.suppress(Exception):
            await client.create_bucket(Bucket=S3_BUCKET)


def _open_s3(addressing_style: str) -> Callable[[], object]:
    @asynccontextmanager
    async def opener() -> AsyncIterator[object]:
        await _s3_ensure_bucket(addressing_style)
        async with connect_key_value_store(
            "s3",
            s3_bucket=S3_BUCKET,
            s3_endpoint=S3_ENDPOINT,
            s3_access_key=S3_ACCESS_KEY,
            s3_secret_key=S3_SECRET_KEY,
            s3_addressing_style=addressing_style,
            policy=ConnectPolicy.fail_fast(),
        ) as store:
            yield KeyValueFileStore(store)

    return opener


# ── leaf backend の「障害を返す transport」（fail-loud 契約を実 backend へ・M065 step5 / M066②） ──
#
# `assert_fail_loud_over_transport` は「既に壊れた下層に繋がった store」が全 op で握り潰さず loud に
# 失敗するかを見る（現状 HTTP 越しの Remote のみ）。leaf backend（nats/s3）は backend 固有の低レベル
# クライアントを **接続後に故障プロキシへ差し替える**ことで、backend 自身のエラー処理（except 節）を
# 実際に通しつつ、欠損（NotFoundError）と区別できない握り潰し（M054/M055 クラス）を炙り出す。


class _FaultObjStore:
    """全メソッドが `InjectedFault` を投げる NATS object store プロキシ（壊れた下層）。"""

    def __getattr__(self, name: str) -> Callable:
        async def _boom(*_a: object, **_k: object) -> object:
            raise InjectedFault(f"nats.obs.{name}")

        return _boom


def _open_nats_faulty() -> Callable[[], object]:
    @asynccontextmanager
    async def opener() -> AsyncIterator[object]:
        async with connect_key_value_store(
            "nats",
            nats_url=NATS_URL,
            nats_bucket="manystore_e2e",
            policy=ConnectPolicy.fail_fast(),
        ) as store:
            store._obs = _FaultObjStore()  # 接続済みの下層 obs を故障プロキシへ差し替える
            yield store

    return opener


class _FaultS3Client:
    """全メソッドが `InjectedFault` を投げる S3 クライアント（壊れた下層）。

    backend の `except client.exceptions.NoSuchKey` 評価のため `.exceptions.NoSuchKey` を備える
    （`InjectedFault` はこれに該当しない＝get_or_raise の欠損偽装に化けず伝播することを見られる）。
    """

    class exceptions:  # noqa: N801  botocore の client.exceptions 名前空間に合わせる
        class NoSuchKey(Exception): ...

    async def get_object(self, **_k: object) -> object:
        raise InjectedFault("s3.get_object")

    async def head_object(self, **_k: object) -> object:
        raise InjectedFault("s3.head_object")

    async def put_object(self, **_k: object) -> object:
        raise InjectedFault("s3.put_object")

    async def delete_object(self, **_k: object) -> object:
        raise InjectedFault("s3.delete_object")

    def get_paginator(self, *_a: object, **_k: object) -> object:
        raise InjectedFault("s3.get_paginator")


@asynccontextmanager
async def _fault_s3_session() -> AsyncIterator[object]:
    yield _FaultS3Client()


def _open_s3_faulty(addressing_style: str) -> Callable[[], object]:
    @asynccontextmanager
    async def opener() -> AsyncIterator[object]:
        await _s3_ensure_bucket(addressing_style)
        async with connect_key_value_store(
            "s3",
            s3_bucket=S3_BUCKET,
            s3_endpoint=S3_ENDPOINT,
            s3_access_key=S3_ACCESS_KEY,
            s3_secret_key=S3_SECRET_KEY,
            s3_addressing_style=addressing_style,
            policy=ConnectPolicy.fail_fast(),
        ) as store:
            store._session = _fault_s3_session  # 接続後に毎オペのクライアントを故障版へ差し替える
            yield store

    return opener


@dataclass
class Provider:
    """1 つの被テスト実装。`open()` が接続済み FileStore を yield する async CM を返す。"""

    id: str
    open: Callable[[], object]  # () -> async context manager（FileStore を yield）
    gated: bool = False  # 実 backend（未到達 skip・slow・実行時エラーも skip 扱い）
    reachable: Callable[[], bool] = field(default=lambda: True)


def all_providers() -> list[Provider]:
    """全 provider の宣言（ここ 1 か所に backend を集約）。"""
    return [
        Provider("dict", _open_dict),
        Provider("local", _open_local),
        Provider("remote", _open_remote),
        Provider("nats", _open_nats, gated=True, reachable=_nats_up),
        Provider("s3-virtual", _open_s3("virtual"), gated=True, reachable=_s3_virtual_up),
        Provider("s3-path", _open_s3("path"), gated=True, reachable=_s3_up),
    ]


def leaf_fault_providers() -> list[Provider]:
    """leaf backend を**故障 transport に繋いだ** provider（fail-loud over transport 用・M066②）。

    `open()` は接続済みだが下層クライアントを故障プロキシへ差し替えた leaf store を yield する。
    `assert_fail_loud_over_transport` を当て、全 op が障害を握り潰さず loud に失敗するかを検査する。
    """
    return [
        Provider("nats-fault", _open_nats_faulty(), gated=True, reachable=_nats_up),
        Provider("s3-path-fault", _open_s3_faulty("path"), gated=True, reachable=_s3_up),
    ]
