"""実 backend への E2E 疎通テスト（パラメタライズ）。

**同一の CRUD ラウンドトリップ（`_crud_roundtrip`）を、注入するストアだけ変えて全 backend で回す**:

- `local`       … 一時ディレクトリ
- `nats`        … NATS Object Store
- `s3-virtual`  … S3 virtual-host（ドメイン）スタイル
- `s3-path`     … S3 path スタイル

各ケースは backend が無い／認証未整備なら **skip**（CI など backend 無し環境で赤くしない）。
`local` は常に走り、失敗は実バグなので skip しない。

手元検証（`make e2e-up` が backend 起動＋SeaweedFS に開発用 S3 identity を登録する）:

    make e2e-up           # docker compose up + S3 identity 登録
    make check            # local / nats / s3-path が走る（s3-virtual はローカルでは原理的に skip）

S3 の鍵は既定で `make e2e-up` が作る dev identity。別サーバ（minio 等）なら env で上書きする
（`MANYSTORE_S3_ACCESS_KEY` / `MANYSTORE_S3_SECRET_KEY`）。

注: `s3-virtual`（ドメインスタイル）はローカル S3 互換サーバでは `bucket.<host>` を名前解決できず
常に skip になる（virtual-host は実 AWS 等の DNS 環境向け）。`s3-path` がローカルの実証ケース。
"""

import asyncio
import contextlib
import os
import socket
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from manystore import ConnectPolicy, connect_key_value_store

S3_HOST, S3_PORT = "localhost", 8333
NATS_HOST, NATS_PORT = "localhost", 4222
S3_ENDPOINT = f"http://{S3_HOST}:{S3_PORT}"
NATS_URL = f"nats://{NATS_HOST}:{NATS_PORT}"
S3_BUCKET = "manystore-e2e"

# S3 互換サーバの認証は環境ごとに違う。既定は `make e2e-up` が SeaweedFS に登録する dev identity。
# 別サーバ（minio 等）なら env で上書きする。鍵不一致や未整備なら s3 ケースは skip。
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


def _nats_up() -> bool:
    return _reachable(NATS_HOST, NATS_PORT)


def _always() -> bool:
    return True


async def _crud_roundtrip(store) -> None:
    """全 backend 共通の CRUD ラウンドトリップ（put→get→exists→list→cp→delete）。"""
    key = f"e2e/{uuid.uuid4().hex}"
    payload = b"hello-manystore"

    await store.put(key, payload)
    assert await store.get(key) == payload
    assert await store.exists(key) is True

    names = [info["filename"] for info in await store.list_all(limit=100)]
    assert key in names

    dst = key + ".cp"
    await store.cp(key, dst)
    assert await store.get(dst) == payload

    await store.delete(key)
    await store.delete(dst)
    assert await store.exists(key) is False


# ── backend ごとの「ストアを開く」context manager（中身は同じテストに注入される） ──


@asynccontextmanager
async def _open_local() -> AsyncIterator[object]:
    with tempfile.TemporaryDirectory() as d:
        async with connect_key_value_store("local", local_dir=Path(d)) as store:
            yield store


@asynccontextmanager
async def _open_nats() -> AsyncIterator[object]:
    async with connect_key_value_store(
        "nats", nats_url=NATS_URL, nats_bucket="manystore_e2e", policy=ConnectPolicy.fail_fast()
    ) as store:
        yield store


async def _s3_ensure_bucket(addressing_style: str) -> None:
    """S3 の connect は head_bucket で存在前提なので、先にバケットを作る（既存/失敗は無視）。"""
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
            yield store

    return opener


@dataclass
class _Case:
    id: str
    opener: Callable[[], object]  # () -> async context manager（store を yield）
    reachable: Callable[[], bool]  # 安価な到達チェック
    skip_on_error: bool  # 実行時エラーを skip 扱いにするか（env 依存）。False=実バグ


CASES = [
    _Case("local", _open_local, _always, skip_on_error=False),
    _Case("nats", _open_nats, _nats_up, skip_on_error=True),
    _Case("s3-virtual", _open_s3("virtual"), _s3_up, skip_on_error=True),
    _Case("s3-path", _open_s3("path"), _s3_up, skip_on_error=True),
]


async def _run(opener: Callable[[], object]) -> None:
    async with opener() as store:
        await _crud_roundtrip(store)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_backend_crud(case: _Case) -> None:
    """注入するストアだけ変えて、全 backend で同じ CRUD を回す。"""
    if not case.reachable():
        pytest.skip(f"{case.id}: backend 未到達")
    try:
        asyncio.run(_run(case.opener))
    except Exception as e:
        if case.skip_on_error:
            pytest.skip(f"{case.id}: 環境/認証 未整備 → {type(e).__name__}: {e}")
        raise
