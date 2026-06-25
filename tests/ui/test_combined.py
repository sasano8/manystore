"""combined 層のテスト（M023）。

統合アプリ（[create_combined_app]）が 2 系統を 1 プロセス・1 共有 [StorageService] で
公開することを検証する:

- `/kv/raw/...`     … manystore ネイティブ REST（buffered・in-process TestClient で疎通）。
- `/storage/s3/...` … S3 互換ゲートウェイ（streaming）。**実 S3 クライアント（aiobotocore）**を
  `endpoint_url=<host>/storage/s3` に向け、PUT/GET/HEAD/List/DELETE/multipart を実往復で検証する。

実 S3 クライアントは実ソケットを使うので、in-process ASGI ではなく uvicorn を
ephemeral port で別スレッド起動する（既存 `test_gateway_s3client.py` と同型の `_ThreadedServer`）。
"""

import socket
import threading
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from manystore.serving.services.config import parse_config
from manystore.serving.services.service import StorageService

uvicorn = pytest.importorskip("uvicorn")

from manystore.serving.combined import create_combined_app  # noqa: E402

_ACCESS_KEY = "gw-test-access"
_SECRET_KEY = "gw-test-secret"


def _make_config(tmp_path: Path):
    return parse_config(
        {
            "contexts": {
                "work": {"backend": "local", "root": str(tmp_path / "work")},
                "ro": {"backend": "local", "root": str(tmp_path / "ro"), "writable": False},
            },
            "views": {"featured": [{"context": "work", "path": "interrupt", "pin": True}]},
            "default_context": "work",
        }
    )


# ── /kv/raw ネイティブ REST 疎通（in-process）──


def test_manystore_native_rest_roundtrip(tmp_path: Path) -> None:
    """`/kv/raw` プレフィクスで manystore ネイティブ REST が疎通する（共有 service）。"""
    from fastapi.testclient import TestClient

    service = StorageService(_make_config(tmp_path), watch_interval=0.05)
    with TestClient(create_combined_app(service)) as client:
        # bucket 一覧（lifespan で service.connect が一度だけ走っていることの証左）。
        meta = client.get("/kv/raw/").json()
        assert {c["name"] for c in meta["contexts"]} == {"work", "ro"}
        assert meta["default_context"] == "work"

        # PUT → GET → DELETE が `/kv/raw/{bucket}/{path}` で通る。
        assert client.put("/kv/raw/work/a/b.txt", content=b"hi").status_code == 204
        r = client.get("/kv/raw/work/a/b.txt")
        assert r.status_code == 200 and r.content == b"hi"
        assert client.delete("/kv/raw/work/a/b.txt").status_code == 204
        assert client.get("/kv/raw/work/a/b.txt").status_code == 404


def test_native_and_s3_share_service(tmp_path: Path) -> None:
    """`/kv/raw` PUT 後に `/storage/s3` GET でも同じ object が見える（service 一本化）。"""
    from fastapi.testclient import TestClient

    service = StorageService(_make_config(tmp_path), watch_interval=0.05)
    with TestClient(create_combined_app(service)) as client:
        assert client.put("/kv/raw/work/shared.txt", content=b"X").status_code == 204
        # storage/s3 側（path-style）から同じ bucket / key で取得できる。
        r = client.get("/storage/s3/work/shared.txt")
        assert r.status_code == 200 and r.content == b"X"


# ── /storage/s3 実 S3 クライアント往復 ──


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ThreadedServer:
    """uvicorn を別スレッドで起動し、起動完了を待ってから endpoint を返す。"""

    def __init__(self, app, host: str, port: int) -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.endpoint = f"http://{host}:{port}"

    def start(self) -> None:
        self._thread.start()
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("combined server thread died during startup")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def combined_endpoint(tmp_path) -> Iterator[str]:
    """ephemeral port で実 listen する統合アプリを起動し、その base endpoint を返す。"""
    service = StorageService(_make_config(tmp_path), watch_interval=0.05)
    app = create_combined_app(service)
    server = _ThreadedServer(app, "127.0.0.1", _free_port())
    server.start()
    try:
        yield server.endpoint
    finally:
        server.stop()


def _make_client(endpoint: str):
    """S3 クライアントは `endpoint_url=<host>/storage/s3` を向ける（統合アプリの S3 prefix）。"""
    from aiobotocore.config import AioConfig
    from aiobotocore.session import get_session

    return get_session().create_client(
        "s3",
        endpoint_url=f"{endpoint}/storage/s3",
        region_name="us-east-1",
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
        config=AioConfig(s3={"addressing_style": "path"}),
    )


@pytest.mark.slow  # uvicorn 別スレッド実 listen＋実 aiobotocore 往復＝待ち支配（R13）
async def test_combined_s3_client_roundtrip(combined_endpoint: str) -> None:
    """統合 `/storage/s3` 経由で実 S3 クライアント PUT→GET→HEAD→List→DELETE が往復する。"""
    payload = b"hello-via-combined-s3"
    key = "dir/sub/object.bin"

    async with _make_client(combined_endpoint) as s3:
        put = await s3.put_object(Bucket="work", Key=key, Body=payload)
        assert put["ResponseMetadata"]["HTTPStatusCode"] == 200
        etag = put["ETag"]

        got = await s3.get_object(Bucket="work", Key=key)
        async with got["Body"] as stream:
            body = await stream.read()
        assert body == payload
        assert got["ContentLength"] == len(payload)
        assert got["ETag"] == etag

        head = await s3.head_object(Bucket="work", Key=key)
        assert head["ContentLength"] == len(payload)

        listed = await s3.list_objects_v2(Bucket="work")
        assert key in {c["Key"] for c in listed.get("Contents", [])}

        await s3.delete_object(Bucket="work", Key=key)
        with pytest.raises(s3.exceptions.NoSuchKey):
            await s3.get_object(Bucket="work", Key=key)


@pytest.mark.slow  # uvicorn 別スレッド実 listen＋実 aiobotocore 往復＝待ち支配（R13）
async def test_combined_s3_client_multipart_roundtrip(combined_endpoint: str) -> None:
    """統合 `/storage/s3` 経由で multipart（Create→UploadPart×3→Complete→GET 一致）が往復する。"""
    key = "big/object.bin"
    part1 = b"A" * (1024 * 1024)
    part2 = b"B" * (512 * 1024)
    part3 = b"C" * 123
    expected = part1 + part2 + part3

    async with _make_client(combined_endpoint) as s3:
        created = await s3.create_multipart_upload(Bucket="work", Key=key)
        upload_id = created["UploadId"]
        assert upload_id

        parts = []
        for n, chunk in enumerate((part1, part2, part3), start=1):
            up = await s3.upload_part(
                Bucket="work", Key=key, UploadId=upload_id, PartNumber=n, Body=chunk
            )
            parts.append({"ETag": up["ETag"], "PartNumber": n})

        completed = await s3.complete_multipart_upload(
            Bucket="work",
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        assert completed["ETag"].rstrip('"').endswith("-3")

        got = await s3.get_object(Bucket="work", Key=key)
        async with got["Body"] as stream:
            body = await stream.read()
        assert body == expected

        listed = await s3.list_objects_v2(Bucket="work")
        keys = {c["Key"] for c in listed.get("Contents", [])}
        assert key in keys
        assert not any(k.startswith(".manystore-mpu") for k in keys)
