"""s3 backend — S3 互換のオブジェクトストア（KVS / 真のストリーミング FileStore）。

aiobotocore はメソッド内で遅延 import する（依存を __init__ 直下に持ち込まない）。
"""

from collections.abc import AsyncIterator

from ...exceptions import UnsupportedOperation
from ...protocols import AsyncFileObject, FileInfo, KeyValueStoreBase


class _S3Base:
    """S3 系ストアの共通接続部（bucket・認証・`_session`）。"""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        addressing_style: str = "virtual",
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url or None
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        # "virtual"（ドメイン。既定）/ "path" / "auto"。S3 互換サーバ（minio / SeaweedFS 等）は
        # virtual だと bucket.<host> を名前解決できないので、利用側が明示的に "path" を指定する。
        self._addressing_style = addressing_style

    def _session(self):
        from aiobotocore.config import AioConfig
        from aiobotocore.session import get_session

        return get_session().create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            config=AioConfig(s3={"addressing_style": self._addressing_style}),
        )

    async def connect(self) -> None:
        # 永続セッションは持たない（毎オペでクライアント生成）。接続確認として bucket 到達を見る。
        async with self._session() as client:
            await client.head_bucket(Bucket=self._bucket)

    async def aclose(self) -> None:
        return None


class S3KeyValueStore(_S3Base, KeyValueStoreBase):
    async def put(self, key: str, value: bytes) -> FileInfo:
        async with self._session() as client:
            await client.put_object(Bucket=self._bucket, Key=key, Body=value)
        return {"filename": key, "size": len(value)}

    async def get_or_raise(self, key: str) -> bytes:
        async with self._session() as client:
            try:
                resp = await client.get_object(Bucket=self._bucket, Key=key)
            except client.exceptions.NoSuchKey as e:
                raise FileNotFoundError(key) from e  # 欠損は FileNotFoundError に正規化
            async with resp["Body"] as stream:
                return await stream.read()

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # ネイティブ prefix 絞り＝サーバ側 `list_objects_v2(Prefix=…)`（空 prefix で全件）。
        # 総なめに落とさず S3 側で絞る。limit は絞り込み後の先頭 N 件。
        async with self._session() as client:
            paginator = client.get_paginator("list_objects_v2")
            objects: list[dict] = []
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                objects.extend(page.get("Contents", []))
        objects.sort(key=lambda o: o["Key"], reverse=True)
        for o in objects[:limit]:  # prefix はサーバ側で済＝先頭 N 件スライス（limit=None は全件）
            yield FileInfo(filename=o["Key"], size=o["Size"])

    async def list_all(self, limit: int | None = None, prefix: str = "") -> list[FileInfo]:
        return [info async for info in self.iter_all(limit, prefix)]

    async def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        async with self._session() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as e:
                # 404/NoSuchKey/NotFound のみ「無い」＝False。認証・5xx・接続断などは
                # 握り潰さず伝播させる（fail-loud）。
                if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise
            return True

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
        raise UnsupportedOperation("not writable")

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
        raise UnsupportedOperation("not readable")

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


class S3FileStore(S3KeyValueStore):
    """S3 の完全な [FileStore]（= [S3KeyValueStore] ＋ 真のストリーミング IO）。

    S3 は **file 寄り**＝streaming（range body / multipart）が強みなので、open_reader/open_writer を
    **native streaming** で実装し（核をこちらに置く）、大きなオブジェクトでも一定メモリで扱える。
    KVS 面（whole get/put・iter/list/exists/delete/cp/mv・connect/aclose）は S3KeyValueStore から
    継承＝小さい値は get_object/put_object の whole が最適（二重持ちしない）。`part_size` は
    multipart の 1 パートサイズ（実 S3 は最終パート以外 5MB 以上が必要。既定 8MiB）。
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        addressing_style: str = "virtual",
        part_size: int = 8 * 1024 * 1024,
    ) -> None:
        super().__init__(bucket, endpoint_url, region, access_key, secret_key, addressing_style)
        self._part_size = part_size

    async def open_reader(self, filename: str) -> AsyncFileObject:
        cm = self._session()
        client = await cm.__aenter__()
        resp = await client.get_object(Bucket=self._bucket, Key=filename)
        return _S3StreamReader(cm, client, resp["Body"])

    async def open_writer(self, filename: str) -> AsyncFileObject:
        return _S3MultipartWriter(self, filename, self._part_size)
