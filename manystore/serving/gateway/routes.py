"""routes — S3 互換 REST エンドポイント（S1: 最小 S3 操作 + S2: multipart）。

manystore を S3 API として公開する薄い HTTP フロント。bucket = manystore の context、
key = object key として既存 [StorageService]（put/get/exists/delete/list_entries）へ 1:1 で乗せる。
コア IF は変更しない（ゲートウェイは IF の上の薄い層）。

対応操作（S1）:
- GET    /{bucket}/{key}                       … GetObject（404→S3 XML エラー）
- PUT    /{bucket}/{key}                       … PutObject（200 + ETag ヘッダ）
- HEAD   /{bucket}/{key}                       … HeadObject（200/404 + Content-Length）
- DELETE /{bucket}/{key}                       … DeleteObject（204）
- GET    /{bucket}?list-type=2&prefix=&delimiter=  … ListObjectsV2（S3 XML）

対応操作（S2 multipart・[multipart] 参照）:
- POST   /{bucket}/{key}?uploads                       … CreateMultipartUpload（uploadId 発行）
- PUT    /{bucket}/{key}?partNumber=N&uploadId=X       … UploadPart（part を一時キーへ）
- POST   /{bucket}/{key}?uploadId=X                    … CompleteMultipartUpload（結合 + ETag）
- DELETE /{bucket}/{key}?uploadId=X                    … AbortMultipartUpload（一時 part 破棄）

S3 操作は同一 HTTP メソッド/パスを query で多重化するため、PUT/POST/DELETE 各ハンドラの
入口で query を見て multipart 操作へ分岐する。**ListParts / ListMultipartUploads は YAGNI で
見送り**（progress.md M021 のバックログ）。

未対応（バックログ）: passthrough（S3）/ continuation token・MaxKeys ページング（繰延）/
ListParts・ListMultipartUploads。SigV4 署名検証はしない（gateway 認証へ委譲＝既定 localhost）。

S3 のキーは `SafeKeyValueStore` の制約（先頭 '/'・'..'・'\\'・NUL を拒否）に従う。
弾かれたキーは 400 InvalidArgument にマップする（制約は意図的に維持＝緩めない）。
"""

import hashlib

from ...storage.surfaces.safe import UnsafePathError
from ..services.s3map import (
    fold_list_v2,
    parse_complete_multipart,
    render_complete_multipart,
    render_error,
    render_initiate_multipart,
    render_list_v2,
)
from ..services.service import ContextNotFound, ReadOnlyContext, StorageService
from . import multipart

_XML = "application/xml"

# S3 互換ゲートウェイを application に include する際の NS prefix（M025・単一正本）。
# combined がこの定数で前置し、S3 クライアントは `endpoint_url=<host>{STORAGE_S3_PREFIX}` を向ける。
STORAGE_S3_PREFIX = "/storage/s3"

# ListObjectsV2 の S1 既定上限（continuation token は未実装＝この上限で打ち切る）。
DEFAULT_MAX_KEYS = 1000


def build_router(service: StorageService):
    """`service` を載せた S3 互換ルートの [APIRouter] を返す。fastapi は遅延 import。

    統合アプリは `app.include_router(build_router(service), prefix="/s3")` で前置でき、
    単体アプリ（[create_gateway]）は prefix なしで include する。相対パス
    （`/{bucket}/{key:path}` 等）は prefix が前置されるだけで本体は不変。
    """
    from fastapi import APIRouter, Request, Response

    app = APIRouter()

    def _error_response(exc: Exception, resource: str) -> Response:
        """manystore の例外を S3 エラー XML レスポンスへマップする。"""
        if isinstance(exc, ContextNotFound):
            return _xml(404, render_error(code="NoSuchBucket", message=str(exc), resource=resource))
        if isinstance(exc, ReadOnlyContext):
            return _xml(403, render_error(code="AccessDenied", message=str(exc), resource=resource))
        if isinstance(exc, UnsafePathError):
            return _xml(
                400, render_error(code="InvalidArgument", message=str(exc), resource=resource)
            )
        if isinstance(exc, multipart.NoSuchUpload):
            return _xml(404, render_error(code="NoSuchUpload", message=str(exc), resource=resource))
        raise exc

    def _not_found(resource: str) -> Response:
        return _xml(404, render_error(code="NoSuchKey", message="key not found", resource=resource))

    # ── ListObjectsV2: GET /{bucket}?list-type=2 ──
    # bucket だけのパス（GET /{bucket} と GET /{bucket}/）は ListObjectsV2 として扱う。
    @app.get("/{bucket}")
    @app.get("/{bucket}/")
    async def list_objects_v2(bucket: str, request: Request) -> Response:
        q = request.query_params
        prefix = q.get("prefix", "")
        delimiter = q.get("delimiter", "")
        max_keys = _parse_max_keys(q.get("max-keys"))
        try:
            # delimiter 畳み込み前に上限+1 まで取り、打ち切り判定に使う。
            entries = await service.list_entries(bucket, prefix=prefix, limit=max_keys + 1)
        except Exception as exc:
            return _error_response(exc, f"/{bucket}")
        # multipart の一時 part（予約プレフィクス）はオブジェクト一覧に出さない。
        entries = [e for e in entries if not multipart.is_reserved_key(e.key)]
        is_truncated = len(entries) > max_keys
        entries = entries[:max_keys]
        contents, common = fold_list_v2(entries, prefix, delimiter)
        body = render_list_v2(
            bucket=bucket,
            prefix=prefix,
            delimiter=delimiter,
            contents=contents,
            common_prefixes=common,
            max_keys=max_keys,
            is_truncated=is_truncated,
        )
        return _xml(200, body)

    # ── HeadObject: HEAD /{bucket}/{key} ──
    @app.head("/{bucket}/{key:path}")
    async def head_object(bucket: str, key: str) -> Response:
        resource = f"/{bucket}/{key}"
        try:
            data = await service.get(bucket, key)
        except Exception as exc:
            return _error_response(exc, resource)
        if data is None:
            return Response(status_code=404)
        return Response(
            status_code=200,
            headers={"Content-Length": str(len(data)), "ETag": _etag(data)},
        )

    # ── GetObject: GET /{bucket}/{key} ──
    @app.get("/{bucket}/{key:path}")
    async def get_object(bucket: str, key: str) -> Response:
        resource = f"/{bucket}/{key}"
        try:
            data = await service.get(bucket, key)
        except Exception as exc:
            return _error_response(exc, resource)
        if data is None:
            return _not_found(resource)
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"ETag": _etag(data)},
        )

    # ── PutObject / UploadPart: PUT /{bucket}/{key} ──
    # uploadId + partNumber が付けば UploadPart（S2）、無ければ通常の PutObject。
    @app.put("/{bucket}/{key:path}")
    async def put_object(bucket: str, key: str, request: Request) -> Response:
        resource = f"/{bucket}/{key}"
        q = request.query_params
        upload_id = q.get("uploadId")
        part_number = q.get("partNumber")
        body = await request.body()
        if upload_id is not None and part_number is not None:
            try:
                pn = _parse_part_number(part_number)
                etag = await multipart.upload_part(service, bucket, upload_id, pn, body)
            except ValueError as exc:
                return _xml(
                    400, render_error(code="InvalidArgument", message=str(exc), resource=resource)
                )
            except Exception as exc:
                return _error_response(exc, resource)
            return Response(status_code=200, headers={"ETag": etag})
        try:
            await service.put(bucket, key, body)
        except Exception as exc:
            return _error_response(exc, resource)
        return Response(status_code=200, headers={"ETag": _etag(body)})

    # ── CreateMultipartUpload / CompleteMultipartUpload: POST /{bucket}/{key} ──
    # `?uploads` で開始、`?uploadId=` で完了（S3 はどちらも POST + query で多重化する）。
    @app.post("/{bucket}/{key:path}")
    async def post_object(bucket: str, key: str, request: Request) -> Response:
        resource = f"/{bucket}/{key}"
        q = request.query_params
        # CreateMultipartUpload: `?uploads`（値なしフラグ）。
        if "uploads" in q:
            try:
                upload_id = await multipart.create_upload(service, bucket)
            except Exception as exc:
                return _error_response(exc, resource)
            return _xml(
                200,
                render_initiate_multipart(bucket=bucket, key=key, upload_id=upload_id),
            )
        # CompleteMultipartUpload: `?uploadId=`。
        upload_id = q.get("uploadId")
        if upload_id is not None:
            body = await request.body()
            try:
                part_numbers = parse_complete_multipart(body)
                etag = await multipart.complete_upload(
                    service, bucket, key, upload_id, part_numbers
                )
            except Exception as exc:
                return _error_response(exc, resource)
            return _xml(
                200,
                render_complete_multipart(bucket=bucket, key=key, etag=etag),
            )
        return _xml(
            400, render_error(code="InvalidArgument", message="unsupported POST", resource=resource)
        )

    # ── DeleteObject / AbortMultipartUpload: DELETE /{bucket}/{key} ──
    # uploadId が付けば AbortMultipartUpload（S2）、無ければ通常の DeleteObject。
    @app.delete("/{bucket}/{key:path}")
    async def delete_object(bucket: str, key: str, request: Request) -> Response:
        resource = f"/{bucket}/{key}"
        upload_id = request.query_params.get("uploadId")
        if upload_id is not None:
            try:
                await multipart.abort_upload(service, bucket, upload_id)
            except Exception as exc:
                return _error_response(exc, resource)
            return Response(status_code=204)
        try:
            await service.delete(bucket, key)
        except Exception as exc:
            return _error_response(exc, resource)
        return Response(status_code=204)

    def _xml(status: int, body: bytes) -> Response:
        return Response(content=body, status_code=status, media_type=_XML)

    return app


def register_routes(app, service: StorageService) -> None:
    """`app`（FastAPI）に S3 互換ルートを登録する（後方互換の薄いシム）。

    内部で [build_router] が返す [APIRouter] を `app.include_router(...)` する。
    既存の単体アプリ生成（[create_gateway]）はこの形のまま動く。
    """
    app.include_router(build_router(service))


def _etag(data: bytes) -> str:
    """S3 互換 ETag（単発 PUT は本体の MD5 を二重引用符で囲む）。"""
    return '"' + hashlib.md5(data).hexdigest() + '"'  # noqa: S324  (ETag 用途・暗号目的でない)


def _parse_part_number(raw: str) -> int:
    """UploadPart の partNumber を検証して返す（S3 は 1..10000）。"""
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"invalid partNumber: {raw!r}") from None
    if not (1 <= value <= 10000):
        raise ValueError(f"partNumber out of range (1..10000): {value}")
    return value


def _parse_max_keys(raw: str | None) -> int:
    """max-keys クエリを既定上限の範囲にクランプする（S1 はページング無しの上限打ち切り）。"""
    if raw is None:
        return DEFAULT_MAX_KEYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_KEYS
    if value <= 0:
        return DEFAULT_MAX_KEYS
    return min(value, DEFAULT_MAX_KEYS)
