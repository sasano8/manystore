"""gateway 層の「実 S3 クライアント往復」テスト（aiobotocore で実ソケット越し）。

既存 `test_gateway.py` は ASGI TestClient（in-process）でゲートウェイ生成の S3 XML を
stdlib ElementTree でパース検証するだけだった。本ファイルはそれに**上乗せ**で、
**実 S3 クライアント（aiobotocore）**を `endpoint_url=<起動したゲートウェイ>` に向け、
PUT→GET→HEAD→ListObjectsV2→DELETE を**実クライアントで往復**させてレスポンス
（本文・ETag・Content-Length・Contents/CommonPrefixes 等）を検証する。

なぜ aiobotocore か:
- manystore はコア依存に `aiobotocore>=2.0.0` を持つ（`backends/s3.py` が使用）。aiobotocore は
  botocore を内包する**実 S3 クライアント**なので、`endpoint_url` をゲートウェイへ向ければ
  **新依存ゼロ**で実クライアント往復が書ける（同期 boto3 を足す必要がない）。

なぜ in-process ASGI ではなく実サーバか:
- aiobotocore は**実ソケット**で通信するため、ASGI in-process transport では往復できない。
  uvicorn を ephemeral port（127.0.0.1:0）で別スレッド起動し、実際に listen させる。

アドレッシングスタイル = path:
- ゲートウェイは `GET /{bucket}/{key}`（bucket=context をパスに置く）＝**path-style** 前提。
  virtual-host（`bucket.<host>`）はローカルでは名前解決できないので必ず `addressing_style="path"`。
"""

import socket
import threading
from collections.abc import Iterator
from contextlib import closing

import pytest

# 全テスト uvicorn 別スレッド listen＋実 aiobotocore 往復＝待ち支配ゆえ module 全体 slow（R13）。
pytestmark = pytest.mark.slow

uvicorn = pytest.importorskip("uvicorn")

from manystore.gateway.app import create_gateway  # noqa: E402
from manystore.implement.config import parse_config  # noqa: E402
from manystore.implement.service import StorageService  # noqa: E402

# 実 S3 クライアント（aiobotocore）への鍵。ゲートウェイは SigV4 を検証しないので任意値でよい。
_ACCESS_KEY = "gw-test-access"
_SECRET_KEY = "gw-test-secret"


def _free_port() -> int:
    """OS に空きポートを 1 つ割り当てさせて番号を返す（ephemeral port）。"""
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
        # uvicorn が実際に listen を開始する（started フラグ）まで待つ。
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("gateway server thread died during startup")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def gateway_endpoint(tmp_path) -> Iterator[str]:
    """ephemeral port で実 listen するゲートウェイを起動し、その endpoint_url を返す。"""
    cfg = parse_config(
        {
            "contexts": {
                "work": {"backend": "local", "root": str(tmp_path / "work")},
                "ro": {"backend": "local", "root": str(tmp_path / "ro"), "writable": False},
            },
            "default_context": "work",
        }
    )
    service = StorageService(cfg, watch_interval=0.05)
    app = create_gateway(service)
    server = _ThreadedServer(app, "127.0.0.1", _free_port())
    server.start()
    try:
        yield server.endpoint
    finally:
        server.stop()


def _make_client(endpoint: str):
    """ゲートウェイへ向けた aiobotocore S3 クライアント（path-style）の context manager。"""
    from aiobotocore.config import AioConfig
    from aiobotocore.session import get_session

    return get_session().create_client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
        config=AioConfig(s3={"addressing_style": "path"}),
    )


async def test_real_s3_client_roundtrip(gateway_endpoint: str) -> None:
    """実 S3 クライアントで PUT→GET→HEAD→ListObjectsV2→DELETE を往復させる。"""
    payload = b"hello-from-real-s3-client"
    key = "dir/sub/object.bin"

    async with _make_client(gateway_endpoint) as s3:
        # ── PutObject: ETag が返り、本文の MD5（gateway 実装）と一致する ──
        put = await s3.put_object(Bucket="work", Key=key, Body=payload)
        assert put["ResponseMetadata"]["HTTPStatusCode"] == 200
        etag = put["ETag"]
        assert etag.startswith('"') and etag.endswith('"')

        # ── GetObject: 本文・Content-Length・ETag が往復で一致する ──
        got = await s3.get_object(Bucket="work", Key=key)
        async with got["Body"] as stream:
            body = await stream.read()
        assert body == payload
        assert got["ContentLength"] == len(payload)
        assert got["ETag"] == etag

        # ── HeadObject: Content-Length / ETag をメタとして取得できる ──
        head = await s3.head_object(Bucket="work", Key=key)
        assert head["ResponseMetadata"]["HTTPStatusCode"] == 200
        assert head["ContentLength"] == len(payload)
        assert head["ETag"] == etag

        # ── ListObjectsV2（フラット）: Contents に対象キーが現れる ──
        listed = await s3.list_objects_v2(Bucket="work")
        keys = {c["Key"] for c in listed.get("Contents", [])}
        assert key in keys

        # ── DeleteObject: 削除後は GetObject が NoSuchKey になる ──
        await s3.delete_object(Bucket="work", Key=key)
        with pytest.raises(s3.exceptions.NoSuchKey):
            await s3.get_object(Bucket="work", Key=key)


async def test_real_s3_client_list_delimiter_common_prefixes(gateway_endpoint: str) -> None:
    """実 S3 クライアントで delimiter='/' の ListObjectsV2＝CommonPrefixes 畳みを検証する。"""
    async with _make_client(gateway_endpoint) as s3:
        await s3.put_object(Bucket="work", Key="top.txt", Body=b"1")
        await s3.put_object(Bucket="work", Key="dir1/x.txt", Body=b"2")
        await s3.put_object(Bucket="work", Key="dir1/y.txt", Body=b"3")
        await s3.put_object(Bucket="work", Key="dir2/z.txt", Body=b"4")

        listed = await s3.list_objects_v2(Bucket="work", Delimiter="/")
        contents = {c["Key"] for c in listed.get("Contents", [])}
        prefixes = {p["Prefix"] for p in listed.get("CommonPrefixes", [])}
        assert contents == {"top.txt"}
        assert prefixes == {"dir1/", "dir2/"}

        # prefix + delimiter で 1 階層下のディレクトリを列挙できる。
        listed = await s3.list_objects_v2(Bucket="work", Prefix="dir1/", Delimiter="/")
        contents = {c["Key"] for c in listed.get("Contents", [])}
        assert contents == {"dir1/x.txt", "dir1/y.txt"}


async def test_real_s3_client_get_missing_raises_nosuchkey(gateway_endpoint: str) -> None:
    """存在しないキーの GetObject が実クライアント上で NoSuchKey として上がる（XML パース経路）。"""
    async with _make_client(gateway_endpoint) as s3:
        with pytest.raises(s3.exceptions.NoSuchKey):
            await s3.get_object(Bucket="work", Key="does-not-exist")


async def test_real_s3_client_readonly_bucket_access_denied(gateway_endpoint: str) -> None:
    """writable=false の context への PutObject が実クライアント上で AccessDenied になる。"""
    from botocore.exceptions import ClientError

    async with _make_client(gateway_endpoint) as s3:
        with pytest.raises(ClientError) as ei:
            await s3.put_object(Bucket="ro", Key="x.txt", Body=b"x")
        assert ei.value.response["Error"]["Code"] == "AccessDenied"


# ── S2: Multipart Upload を実 S3 クライアントで往復検証 ──


async def test_real_s3_client_multipart_roundtrip(gateway_endpoint: str) -> None:
    """実 S3 クライアントで Create→複数 UploadPart→Complete→GET 一致を往復検証する。

    part サイズは S3 の規約（最終以外 5MiB 以上）には縛られない（gateway は単純結合）が、
    複数 part を結合した結果が GET の本文と一致することを確認する。
    """
    key = "big/object.bin"
    part1 = b"A" * (1024 * 1024)  # 1 MiB
    part2 = b"B" * (512 * 1024)  # 0.5 MiB
    part3 = b"C" * 123  # 端数
    expected = part1 + part2 + part3

    async with _make_client(gateway_endpoint) as s3:
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
        # multipart ETag は `<md5hex>-<partCount>` 形式（末尾 -3）。
        assert completed["ETag"].rstrip('"').endswith("-3")

        # GET で結合結果が一致する。
        got = await s3.get_object(Bucket="work", Key=key)
        async with got["Body"] as stream:
            body = await stream.read()
        assert body == expected
        assert got["ContentLength"] == len(expected)

        # 一時 part は ListObjectsV2 に現れない（予約プレフィクスを隠す）。
        listed = await s3.list_objects_v2(Bucket="work")
        keys = {c["Key"] for c in listed.get("Contents", [])}
        assert key in keys
        assert not any(k.startswith(".manystore-mpu") for k in keys)


async def test_real_s3_client_multipart_abort_discards_parts(gateway_endpoint: str) -> None:
    """実 S3 クライアントで Create→UploadPart→Abort 後、本オブジェクトが未作成なことを検証する。"""
    key = "aborted/object.bin"
    async with _make_client(gateway_endpoint) as s3:
        created = await s3.create_multipart_upload(Bucket="work", Key=key)
        upload_id = created["UploadId"]
        await s3.upload_part(
            Bucket="work", Key=key, UploadId=upload_id, PartNumber=1, Body=b"Z" * 1024
        )

        await s3.abort_multipart_upload(Bucket="work", Key=key, UploadId=upload_id)

        # 本オブジェクトは未作成（complete していない）。
        with pytest.raises(s3.exceptions.NoSuchKey):
            await s3.get_object(Bucket="work", Key=key)

        # 一時 part も掃除済み＝一覧に何も残らない。
        listed = await s3.list_objects_v2(Bucket="work")
        keys = {c["Key"] for c in listed.get("Contents", [])}
        assert not any(k.startswith(".manystore-mpu") for k in keys)
