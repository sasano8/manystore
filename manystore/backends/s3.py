"""s3 backend — S3 互換のオブジェクトストア（KVS / 真のストリーミング FileStore）。

aiobotocore はメソッド内で遅延 import する（依存を __init__ 直下に持ち込まない）。
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator

from ..async_storage import FileInfo, FileObject, _take


class _S3Base:
    """S3 系ストアの共通接続部（bucket・認証・`_session`）。"""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url or None
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key

    def _session(self):
        from aiobotocore.session import get_session

        return get_session().create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    async def connect(self) -> None:
        # 永続セッションは持たない（毎オペでクライアント生成）。接続確認として bucket 到達を見る。
        async with self._session() as client:
            await client.head_bucket(Bucket=self._bucket)

    async def aclose(self) -> None:
        return None


class S3KeyValueStore(_S3Base):
    async def put(self, key: str, value: bytes) -> None:
        async with self._session() as client:
            await client.put_object(Bucket=self._bucket, Key=key, Body=value)

    async def get(self, key: str) -> bytes | None:
        async with self._session() as client:
            try:
                resp = await client.get_object(Bucket=self._bucket, Key=key)
                async with resp["Body"] as stream:
                    return await stream.read()
            except client.exceptions.NoSuchKey:
                return None

    async def iter(self) -> AsyncIterator[FileInfo]:
        async with self._session() as client:
            paginator = client.get_paginator("list_objects_v2")
            objects: list[dict] = []
            async for page in paginator.paginate(Bucket=self._bucket):
                objects.extend(page.get("Contents", []))
        objects.sort(key=lambda o: o["Key"], reverse=True)
        for o in objects:
            yield FileInfo(filename=o["Key"], size=o["Size"])

    async def list(self, limit: int = 10) -> list[FileInfo]:
        return await _take(self.iter(), limit)

    async def exists(self, key: str) -> bool:
        async with self._session() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False

    async def delete(self, key: str) -> None:
        async with self._session() as client:
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def cp(self, src: str, dst: str) -> None:
        async with self._session() as client:
            await client.copy_object(
                Bucket=self._bucket,
                Key=dst,
                CopySource={"Bucket": self._bucket, "Key": src},
            )

    async def mv(self, src: str, dst: str) -> None:
        await self.cp(src, dst)  # S3 にネイティブの move は無い
        await self.delete(src)


# ── 真のストリーミング FileStore（全体バッファしない） ──


class _S3StreamReader:
    """`get_object` のストリーム body を read で逐次読み出す（全体をメモリに載せない）。

    body / client の接続は close まで開いたままにする（ストリームを跨いで読むため）。
    """

    def __init__(self, client_cm, client, body) -> None:
        self._client_cm = client_cm
        self._body = body

    async def read(self, size: int = -1) -> bytes:
        return await self._body.read() if size < 0 else await self._body.read(size)

    async def write(self, data: bytes) -> int:
        raise io.UnsupportedOperation("not writable")

    async def close(self) -> None:
        self._body.close()
        await self._client_cm.__aexit__(None, None, None)

    async def __aenter__(self) -> _S3StreamReader:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class _S3MultipartWriter:
    """書き込みを multipart upload でパート分割アップロードする（全体バッファしない）。

    `part_size` ごとに upload_part し、close で残りを最終パート（5MB 未満可）として送って
    complete する。1 バイトも書かれなければ空オブジェクトを単純 put する。
    """

    def __init__(self, base: _S3Base, key: str, part_size: int) -> None:
        self._base = base
        self._key = key
        self._part_size = part_size
        self._buf = bytearray()
        self._parts: list[dict] = []
        self._upload_id: str | None = None
        self._client_cm = None
        self._client = None
        self._closed = False

    async def read(self, size: int = -1) -> bytes:
        raise io.UnsupportedOperation("not readable")

    async def _start(self) -> None:
        self._client_cm = self._base._session()
        self._client = await self._client_cm.__aenter__()
        resp = await self._client.create_multipart_upload(Bucket=self._base._bucket, Key=self._key)
        self._upload_id = resp["UploadId"]

    async def _flush(self, size: int) -> None:
        chunk = bytes(self._buf[:size])
        del self._buf[:size]
        n = len(self._parts) + 1
        resp = await self._client.upload_part(
            Bucket=self._base._bucket,
            Key=self._key,
            PartNumber=n,
            UploadId=self._upload_id,
            Body=chunk,
        )
        self._parts.append({"PartNumber": n, "ETag": resp["ETag"]})

    async def write(self, data: bytes) -> int:
        if self._upload_id is None:
            await self._start()
        self._buf.extend(data)
        while len(self._buf) >= self._part_size:
            await self._flush(self._part_size)
        return len(data)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._upload_id is None:
            # 何も書かれていない → 空オブジェクトを単純 put（multipart は 0 パート不可）。
            cm = self._base._session()
            client = await cm.__aenter__()
            try:
                await client.put_object(Bucket=self._base._bucket, Key=self._key, Body=b"")
            finally:
                await cm.__aexit__(None, None, None)
            return
        try:
            if self._buf:
                await self._flush(len(self._buf))
            await self._client.complete_multipart_upload(
                Bucket=self._base._bucket,
                Key=self._key,
                UploadId=self._upload_id,
                MultipartUpload={"Parts": self._parts},
            )
        finally:
            await self._client_cm.__aexit__(None, None, None)

    async def __aenter__(self) -> _S3MultipartWriter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class S3FileStore(_S3Base):
    """S3 の真のストリーミング [FileStore]（read=body 逐次 / write=multipart）。

    全体をメモリに載せる KeyValueFileStore と違い、大きなオブジェクトでも一定メモリで扱える。
    `part_size` は multipart の 1 パートサイズ（実 S3 は最終パート以外 5MB 以上が必要。既定 8MiB）。
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        part_size: int = 8 * 1024 * 1024,
    ) -> None:
        super().__init__(bucket, endpoint_url, region, access_key, secret_key)
        self._part_size = part_size

    async def open(self, filename: str, mode: str = "rb") -> FileObject:
        if "r" in mode:
            cm = self._session()
            client = await cm.__aenter__()
            resp = await client.get_object(Bucket=self._bucket, Key=filename)
            return _S3StreamReader(cm, client, resp["Body"])
        if "w" in mode:
            return _S3MultipartWriter(self, filename, self._part_size)
        raise ValueError(f"unsupported mode for S3FileStore: {mode!r}")
