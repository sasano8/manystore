"""共有 fake（backend の低層トランスポート模型）— conformance / unit の双方で使う（M074）。

**位置づけ**: backend（S3/NATS）の**低層クライアントだけ in-memory fake に差し替える**
（adapter 自体は本物が走る）。**docker 無し fast** で同じ conformance 契約を流せる（網羅＋fault）。

**権威の所在（重要）**: 並行/CAS/耐久性の意味論は fake では再現しない（単一プロセス in-memory）。
その認証は**実 backend（gated）＋決定的 white-box**に残す。fake が忠実であるべきは観測契約
（CRUD・メタ・fail-loud）で、ズレたら実 backend の同契約が CI で炙り出す。
詳細は `docs/implementing_a_backend.md`。

差し替え方:
- S3: `store._session = lambda: FakeS3()`（`_session()` が返す async client CM を fake に）。
- NATS: `patch_nats_obs(store, FakeNatsObs())`（lazy connect の `_get_obs` を fake に）。
"""

import io


class FakeBody:
    """S3 `get_object` の `Body`（`.read(size)` を持つ async ストリーム）。"""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read() if size is None or size < 0 else self._buf.read(size)

    def close(self) -> None:
        self._buf.close()

    async def __aenter__(self) -> FakeBody:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._buf.close()


class FakeS3:
    """`S3FileStore`/`S3KeyValueStore` を駆動する最小の in-memory S3 client（async・CM 兼）。"""

    class exceptions:  # noqa: N801  aiobotocore の client.exceptions.NoSuchKey 形に合わせる
        class NoSuchKey(Exception): ...

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.meta: dict[str, dict] = {}  # key -> x-amz-meta-*（M013 の sha256 等）
        self._uploads: dict[str, dict] = {}
        self._uid = 0
        self.head_error_code: str | None = None  # set して head_object のエラー Code を差し替える

    async def __aenter__(self) -> FakeS3:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def head_bucket(self, Bucket: str) -> dict:
        return {}  # connect() の存在確認（fake は常に存在）

    async def head_object(self, Bucket: str, Key: str) -> dict:
        from botocore.exceptions import ClientError

        if self.head_error_code is not None:  # fail-loud 検証用（404 以外のエラー）
            raise ClientError({"Error": {"Code": self.head_error_code}}, "HeadObject")
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")  # 実 client と同形
        return {"ContentLength": len(self.objects[Key]), "Metadata": self.meta.get(Key, {})}

    async def create_multipart_upload(self, Bucket: str, Key: str) -> dict:
        self._uid += 1
        uid = f"u{self._uid}"
        self._uploads[uid] = {"key": Key, "parts": {}}
        return {"UploadId": uid}

    async def upload_part(self, Bucket, Key, PartNumber, UploadId, Body) -> dict:
        self._uploads[UploadId]["parts"][PartNumber] = bytes(Body)
        return {"ETag": f'"etag{PartNumber}"'}

    async def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload) -> dict:
        up = self._uploads.pop(UploadId)
        order = [p["PartNumber"] for p in MultipartUpload["Parts"]]
        self.objects[Key] = b"".join(up["parts"][n] for n in order)
        return {}

    async def put_object(self, Bucket, Key, Body, Metadata=None, **_kw) -> dict:
        self.objects[Key] = bytes(Body)
        self.meta[Key] = dict(Metadata or {})  # x-amz-meta-* を保持（head で返す・M013）
        return {}

    async def get_object(self, Bucket, Key) -> dict:
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey  # 欠損は NoSuchKey（実 client と同形）
        return {"Body": FakeBody(self.objects[Key])}

    async def delete_object(self, Bucket, Key) -> dict:
        self.objects.pop(Key, None)  # 欠損 delete は no-op（冪等）
        self.meta.pop(Key, None)
        return {}

    async def copy_object(self, Bucket, Key, CopySource, **_kw) -> dict:
        # CopySource = {"Bucket":.., "Key":..}（backend の cp が渡す形）。欠損は NoSuchKey。
        src = CopySource["Key"] if isinstance(CopySource, dict) else CopySource.split("/", 1)[1]
        if src not in self.objects:
            raise self.exceptions.NoSuchKey
        self.objects[Key] = self.objects[src]
        self.meta[Key] = dict(self.meta.get(src, {}))
        return {}

    def get_paginator(self, name: str) -> FakeS3Paginator:
        assert name == "list_objects_v2"
        return FakeS3Paginator(self)


class FakeS3Paginator:
    """`list_objects_v2` のページャ fake。`Prefix=` をサーバ側で効かせる（実 S3 と同形）。"""

    def __init__(self, fake: FakeS3) -> None:
        self._fake = fake

    async def _pages(self, Bucket: str, Prefix: str = ""):
        contents = [
            {"Key": k, "Size": len(v)}
            for k, v in self._fake.objects.items()
            if k.startswith(Prefix)  # サーバ側 prefix 絞り
        ]
        yield {"Contents": contents}

    def paginate(self, Bucket: str, Prefix: str = ""):
        return self._pages(Bucket, Prefix)


class FakeObjResult:
    """nats-py `obs.get()` の戻り（`.data` を持つ）。"""

    def __init__(self, data: bytes) -> None:
        self.data = data


class FakeObjInfo:
    """nats-py `obs.get_info()`/`list()` の要素。"""

    def __init__(self, name: str, size: int, deleted: bool = False) -> None:
        self.name = name
        self.size = size
        self.deleted = deleted


class FakeNatsObs:
    """最小の fake object store（nats-py の get/get_info/put/delete/list に合わせる）。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, name: str, data, meta=None) -> None:
        self.objects[name] = bytes(data)

    async def get(self, name: str, writeinto=None, show_deleted=False) -> FakeObjResult:
        if name not in self.objects:
            from nats.js.errors import ObjectNotFoundError

            raise ObjectNotFoundError  # 実 nats-py と同形（欠損）
        return FakeObjResult(self.objects[name])

    async def get_info(self, name: str, show_deleted=False) -> FakeObjInfo:
        if name not in self.objects:
            from nats.js.errors import ObjectNotFoundError

            raise ObjectNotFoundError
        return FakeObjInfo(name, len(self.objects[name]))

    async def delete(self, name: str) -> None:
        self.objects.pop(name, None)

    async def list(self, ignore_deletes=False) -> list[FakeObjInfo]:
        infos = [FakeObjInfo(n, len(v)) for n, v in self.objects.items()]
        if not infos:
            from nats.js.errors import NotFoundError

            raise NotFoundError  # 実 nats-py は空ストアで NotFoundError を上げる
        return infos


def patch_nats_obs(store: object, fake: FakeNatsObs) -> None:
    """NATS backend の lazy connect（`_get_obs`）を fake に差し替える（実接続しない）。"""

    async def fake_get_obs() -> FakeNatsObs:
        return fake

    store._get_obs = fake_get_obs
