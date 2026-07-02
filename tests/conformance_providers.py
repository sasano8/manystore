"""挙動契約を流す FileStore プロバイダの**単一宣言**（Dict / Local / Remote / 実 backend）。

`KeyValueStore` / `FileStore` の IF が揃うので、同一の契約 body（run_light / run_middle /
writer all-or-nothing / 並行 CAS / CRUD）を**注入するストアだけ変えて**全実装で回せる。ここに
backend を 1 か所だけ宣言し、`test_conformance_matrix` が各契約を全 provider に流す。

各 provider は **接続済み full Store** を yield する async CM。M071 で backend は 1 クラス
（put/get も open_* も持つ＝旧 `KeyValueFileStore` の wrap は不要）。

フラグ:
- `gated` … 実 backend（未到達なら skip・`slow` マーク）。**到達できる限り実走**＝接続/契約の
  失敗はもう skip に化けさせない（M061）。
- `unsupported` … その**実装が保証しない契約キー**の集合。matrix がその (provider, 契約) を
  `xfail(非strict)` にする＝既知の能力差を「暗黙 skip」でなく**明示の行**として表に出す。能力差は
  flaky（例: SeaweedFS の CAS は時々強制・時々二重成功）なので strict にはしない＝XFAIL/XPASS の
  揺れ自体が「保証なし」を物語る（strict だと XPASS のたびに CI を割る）。

**S3 は実装ごとにマトリクス化**する（`S3_IMPLS`）。条件付き PUT（create-only / update CAS）の
原子強制は実装差が大きい＝**SeaweedFS は非対応**（同時 create が二重成功）／**MinIO は対応**
（AWS S3 セマンティクスを忠実に追従）。この差を unsupported で宣言し matrix に載せる。

run_light/run_middle は **非破壊**（uuid 名前空間に閉じて操作し後始末する・M066①）なので、実共有
backend（nats/s3）にもそのまま流せる。
"""

import contextlib
import os
import socket
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from manystore import ConnectPolicy, connect_key_value_store
from manystore.client import RemoteStore
from manystore.serving.server.app import create_app
from manystore.serving.server.routes import KV_RAW_PREFIX
from manystore.serving.services.config import parse_config
from manystore.serving.services.service import StorageService
from manystore.spec.conformancer import InjectedFault
from manystore.storage.backends import LocalStore, create_unsafe_file_store, get_backend_spec
from manystore.storage.connect import connecting

# ── 実 backend の接続情報（`make e2e-up` の dev 既定＝CI compose と一致。env で上書き）。 ──
NATS_HOST, NATS_PORT = "localhost", 4222
NATS_URL = f"nats://{NATS_HOST}:{NATS_PORT}"
S3_BUCKET = "manystore-e2e"


@dataclass(frozen=True)
class S3Impl:
    """1 つの S3 互換実装の接続先と能力差。`unsupported` は満たさない契約キー（xfail strict）。"""

    id: str  # provider id の接尾（`s3-<id>-<style>`）
    host: str
    port: int
    access: str
    secret: str
    unsupported: frozenset[str] = frozenset()

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"


# 条件付き PUT（put_if_absent=create-only / put_if_match=update CAS）の原子強制が実装差の核。
# SeaweedFS は同時 create が二重成功（非原子）＝非対応。MinIO は AWS 準拠で対応。実測で裏取り済。
S3_IMPLS: list[S3Impl] = [
    S3Impl(
        "seaweedfs",
        "localhost",
        8333,
        os.environ.get("MANYSTORE_S3_ACCESS_KEY", "manystore"),
        os.environ.get("MANYSTORE_S3_SECRET_KEY", "manystoresecret123"),
        unsupported=frozenset({"put_if_absent", "put_if_match"}),
    ),
    S3Impl(
        "minio",
        "localhost",
        9000,
        os.environ.get("MANYSTORE_MINIO_ACCESS_KEY", "minioadmin"),
        os.environ.get("MANYSTORE_MINIO_SECRET_KEY", "minioadmin"),
    ),
]
_S3_BY_ID = {impl.id: impl for impl in S3_IMPLS}


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _impl_up(impl: S3Impl) -> Callable[[], bool]:
    return lambda: _reachable(impl.host, impl.port)


def _s3_virtual_up() -> bool:
    # virtual-host は `<bucket>.<host>` を解決できる DNS 環境のみ成立（ローカルは即 skip・R13）。
    seaweed = _S3_BY_ID["seaweedfs"]
    return bool(os.environ.get("MANYSTORE_S3_VIRTUAL")) and _reachable(seaweed.host, seaweed.port)


def _nats_up() -> bool:
    return _reachable(NATS_HOST, NATS_PORT)


def backend_reachability() -> list[tuple[str, bool]]:
    """CI（`MANYSTORE_E2E_REQUIRED`）で「立てたはずの backend が落ちていないか」を検める一覧。

    compose で起動する nats／各 S3 実装の到達性。s3-virtual（DNS 依存）は CI 既定では立てないので
    含めない＝未到達でも skip のまま（必須ではない）。
    """
    return [("nats", _nats_up()), *[(impl.id, _impl_up(impl)()) for impl in S3_IMPLS]]


# ── registry 駆動の汎用 provider（M077）──
#
# store の**構築は registry**（`BackendSpec.kv_factory`/`file_factory`＝M068）に委ね、テスト側で
# construct を再実装しない。各 backend は `BackendProfile`（接続 opts・reachability・gated 等）の
# **宣言 1 つ**にでき、`_profile_opener` が construct→connect→cleanup を一元化（ベタ実装削減）。
# per-open リソース（local tmp dir・remote の in-process サーバ・fault 注入）は下の custom opener を
# 残す（registry はテスト環境の結線を知らない＝そこだけ手当て）。


def _build_filestore(backend: str, opts: dict) -> object:
    """registry の単一 factory から（未接続の）full Store を作る（M071＝backend は 1 クラス）。"""
    return get_backend_spec(backend).factory(**opts)


@dataclass(frozen=True)
class BackendProfile:
    """「この backend をここでどう試すか」の宣言。構築は registry・結線だけここに書く（M077）。"""

    id: str
    backend: str  # registry 名
    opts: dict = field(default_factory=dict)
    gated: bool = False
    reachable: Callable[[], bool] = lambda: True
    unsupported: frozenset[str] = frozenset()
    setup: Callable[[], Awaitable[None]] | None = None  # 実行前の準備（s3 bucket 作成 等）


def _profile_opener(p: BackendProfile) -> Callable[[], object]:
    @asynccontextmanager
    async def opener() -> AsyncIterator[object]:
        if p.setup is not None:
            await p.setup()  # 実 backend の準備（bucket 作成 等）＝結線の一部
        async with connecting(
            lambda: _build_filestore(p.backend, p.opts),
            policy=ConnectPolicy.fail_fast(),
        ) as store:
            yield store

    return opener


def _profile_provider(p: BackendProfile) -> Provider:
    return Provider(
        p.id, _profile_opener(p), gated=p.gated, reachable=p.reachable, unsupported=p.unsupported
    )


# ── custom opener（per-open リソース／特殊構築が要るもの＝registry だけでは作れない）──


@asynccontextmanager
async def _open_local() -> AsyncIterator[object]:
    with tempfile.TemporaryDirectory() as d:
        yield LocalStore(Path(d))


@asynccontextmanager
async def _open_remote() -> AsyncIterator[object]:
    # client → server → local(tmp) を in-process ASGI で繋ぐ（外部不要・揮発＝isolated）。
    with tempfile.TemporaryDirectory() as d:
        cfg = parse_config({"contexts": {"work": {"backend": "local", "root": d}}})
        service = StorageService(cfg, watch_interval=1.0)
        await service.connect()
        app = create_app(service)
        remote = RemoteStore(
            f"http://test{KV_RAW_PREFIX}", "work", transport=httpx.ASGITransport(app=app)
        )
        try:
            yield remote  # RemoteStore は full Store（open_* は基底合成・M071＝wrap 不要）
        finally:
            await remote.aclose()
            await service.aclose()


# ── fake provider（低層クライアントを in-memory fake に差し替え・非 gated＝docker 無し fast）──
#
# adapter は本物が走り、aiobotocore/nats-py だけ fake に。docker 無しで契約を流す（網羅）。
# **並行/CAS は fake では非権威**（単一プロセス）＝`unsupported` で CAS を xfail（認証は実 backend
# gated＋決定的 white-box・M074）。詳細は `docs/implementing_a_backend.md`。

#: fake が意味論を再現しない契約（＝fake では非権威＝xfail）。実 backend/決定的テストが認証する。
_FAKE_NON_AUTHORITATIVE = frozenset({"put_if_absent", "put_if_match"})


@asynccontextmanager
async def _open_s3_fake() -> AsyncIterator[object]:
    from fakes import FakeS3

    store = create_unsafe_file_store("s3", s3_bucket="fake")
    fake = FakeS3()  # 1 個を使い回す（毎回新インスタンスだと状態が消える）
    store._session = lambda: fake  # 低層 aiobotocore client を fake に（adapter は本物が走る）
    await store.connect()  # head_bucket（fake は常に存在）
    try:
        yield store
    finally:
        await store.aclose()


async def _s3_ensure_bucket(impl: S3Impl, addressing_style: str) -> None:
    from aiobotocore.config import AioConfig
    from aiobotocore.session import get_session

    session = get_session()
    async with session.create_client(
        "s3",
        endpoint_url=impl.endpoint,
        region_name="us-east-1",
        aws_access_key_id=impl.access,
        aws_secret_access_key=impl.secret,
        config=AioConfig(s3={"addressing_style": addressing_style}),
    ) as client:
        with contextlib.suppress(Exception):
            await client.create_bucket(Bucket=S3_BUCKET)


def _s3_opts(impl: S3Impl, addressing_style: str) -> dict:
    """S3 の接続 opts（registry factory 用の flat kwargs）。profile に載せる。"""
    return {
        "s3_bucket": S3_BUCKET,
        "s3_endpoint": impl.endpoint,
        "s3_access_key": impl.access,
        "s3_secret_key": impl.secret,
        "s3_addressing_style": addressing_style,
    }


def _s3_setup(impl: S3Impl, addressing_style: str) -> Callable[[], Awaitable[None]]:
    """profile の `setup`＝接続前に bucket を用意する（テスト環境の結線＝registry は知らない）。"""

    async def setup() -> None:
        await _s3_ensure_bucket(impl, addressing_style)

    return setup


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


def _open_s3_faulty(impl: S3Impl, addressing_style: str) -> Callable[[], object]:
    @asynccontextmanager
    async def opener() -> AsyncIterator[object]:
        await _s3_ensure_bucket(impl, addressing_style)
        async with connect_key_value_store(
            "s3",
            s3_bucket=S3_BUCKET,
            s3_endpoint=impl.endpoint,
            s3_access_key=impl.access,
            s3_secret_key=impl.secret,
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
    gated: bool = (
        False  # 実 backend（未到達なら skip・slow）。到達できる限り実走＝失敗は skip にしない
    )
    reachable: Callable[[], bool] = field(default=lambda: True)
    unsupported: frozenset[str] = frozenset()  # 満たさない契約キー（matrix が xfail strict）


def _gated_profiles() -> list[BackendProfile]:
    """registry 駆動で構築できる gated backend の宣言（構築は registry・結線だけここ・M077）。"""
    seaweed = _S3_BY_ID["seaweedfs"]
    profiles = [
        BackendProfile(
            "nats",
            "nats",
            opts={"nats_url": NATS_URL, "nats_bucket": "manystore_e2e"},
            gated=True,
            reachable=_nats_up,
        ),
    ]
    # S3 は実装ごとに path-style（能力差は unsupported で xfail）。bucket 作成は setup で。
    for impl in S3_IMPLS:
        profiles.append(
            BackendProfile(
                f"s3-{impl.id}-path",
                "s3",
                opts=_s3_opts(impl, "path"),
                gated=True,
                reachable=_impl_up(impl),
                unsupported=impl.unsupported,
                setup=_s3_setup(impl, "path"),
            )
        )
    # virtual-host は SeaweedFS で代表（DNS 環境のみ・既定 skip）。
    profiles.append(
        BackendProfile(
            "s3-seaweedfs-virtual",
            "s3",
            opts=_s3_opts(seaweed, "virtual"),
            gated=True,
            reachable=_s3_virtual_up,
            unsupported=seaweed.unsupported,
            setup=_s3_setup(seaweed, "virtual"),
        )
    )
    return profiles


def all_providers() -> list[Provider]:
    """全 provider の宣言（backend を 1 か所に集約）。構築は registry・profile 駆動（M068/M077）。

    - profile で作れるもの（memory/nats/s3-real）は宣言 1 つ（`_profile_provider`＝construct/connect
      を registry に委譲）。
    - per-open リソース／特殊構築（local の tmp dir・remote の in-process サーバ・s3-fake の client
      差し替え）は custom opener を残す。
    """
    return [
        # memory=dict（run_* のオラクルと同型・full Store）。registry の単一 factory から構築。
        _profile_provider(BackendProfile("dict", "memory")),
        Provider("local", _open_local),  # tmp dir（per-open）＝custom
        Provider("remote", _open_remote),  # in-process ASGI サーバ＝custom
        # fake＝非 gated（docker 無し fast）。CAS は非権威＝xfail。nats fake は JetStream メタ再現が
        # 要るため未 wire（M076）。
        Provider("s3-fake", _open_s3_fake, unsupported=_FAKE_NON_AUTHORITATIVE),
        *[_profile_provider(p) for p in _gated_profiles()],  # 実 backend＝registry 駆動
    ]


def leaf_fault_providers() -> list[Provider]:
    """leaf backend を**故障 transport に繋いだ** provider（fail-loud over transport 用・M066②）。

    `open()` は接続済みだが下層クライアントを故障プロキシへ差し替えた leaf store を yield する。
    `assert_fail_loud_over_transport` を当て、全 op が障害を握り潰さず loud に失敗するかを検査する。
    検査対象は backend 自身の except 節（欠損偽装の有無）＝S3 は実装差が出ない
    1 つ（seaweedfs）で足る。
    """
    seaweed = _S3_BY_ID["seaweedfs"]
    return [
        Provider("nats-fault", _open_nats_faulty(), gated=True, reachable=_nats_up),
        Provider(
            "s3-seaweedfs-path-fault",
            _open_s3_faulty(seaweed, "path"),
            gated=True,
            reachable=_impl_up(seaweed),
        ),
    ]


def native_file_providers() -> list[Provider]:
    """**native streaming IO**（S3=multipart/range）を検査する provider（M066③）。

    M071 で S3 は 1 クラス `S3Store`（両軸 native）に統合＝main の s3 provider と同一構築だが、
    multipart/range を明示的に検査する系統として別 id（`s3-<impl>-path-native`）で残す。
    """
    return [
        _profile_provider(
            BackendProfile(
                f"s3-{impl.id}-path-native",
                "s3",
                opts=_s3_opts(impl, "path"),
                gated=True,
                reachable=_impl_up(impl),
                unsupported=impl.unsupported,
                setup=_s3_setup(impl, "path"),
            )
        )
        for impl in S3_IMPLS
    ]
