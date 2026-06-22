"""routes — S3 互換 REST エンドポイント（S1: 最小 S3 操作）。

manystore を S3 API として公開する薄い HTTP フロント。bucket = manystore の context、
key = object key として既存 [StorageService]（put/get/exists/delete/list_entries）へ 1:1 で乗せる。
コア IF は変更しない（ゲートウェイは IF の上の薄い層）。

対応操作（S1）:
- GET    /{bucket}/{key}                       … GetObject（404→S3 XML エラー）
- PUT    /{bucket}/{key}                       … PutObject（200 + ETag ヘッダ）
- HEAD   /{bucket}/{key}                       … HeadObject（200/404 + Content-Length）
- DELETE /{bucket}/{key}                       … DeleteObject（204）
- GET    /{bucket}?list-type=2&prefix=&delimiter=  … ListObjectsV2（S3 XML）

未対応（バックログ）: multipart（S2）/ passthrough（S3）/ continuation token・MaxKeys
ページング（繰延）。SigV4 署名検証はしない（gateway 自身の認証層に委譲＝既定 localhost）。

S3 のキーは `SafeKeyValueStore` の制約（先頭 '/'・'..'・'\\'・NUL を拒否）に従う。
弾かれたキーは 400 InvalidArgument にマップする（制約は意図的に維持＝緩めない）。
"""

import hashlib

from ..implement.s3map import fold_list_v2, render_error, render_list_v2
from ..implement.service import ContextNotFound, ReadOnlyContext, StorageService
from ..safe_path import UnsafePathError

_XML = "application/xml"

# ListObjectsV2 の S1 既定上限（continuation token は未実装＝この上限で打ち切る）。
DEFAULT_MAX_KEYS = 1000


def register_routes(app, service: StorageService) -> None:
    """`app`（FastAPI）に S3 互換ルートを登録する。fastapi は遅延 import。"""
    from fastapi import Request, Response

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

    # ── PutObject: PUT /{bucket}/{key} ──
    @app.put("/{bucket}/{key:path}")
    async def put_object(bucket: str, key: str, request: Request) -> Response:
        resource = f"/{bucket}/{key}"
        body = await request.body()
        try:
            await service.put(bucket, key, body)
        except Exception as exc:
            return _error_response(exc, resource)
        return Response(status_code=200, headers={"ETag": _etag(body)})

    # ── DeleteObject: DELETE /{bucket}/{key} ──
    @app.delete("/{bucket}/{key:path}")
    async def delete_object(bucket: str, key: str) -> Response:
        resource = f"/{bucket}/{key}"
        try:
            await service.delete(bucket, key)
        except Exception as exc:
            return _error_response(exc, resource)
        return Response(status_code=204)

    def _xml(status: int, body: bytes) -> Response:
        return Response(content=body, status_code=status, media_type=_XML)


def _etag(data: bytes) -> str:
    """S3 互換 ETag（単発 PUT は本体の MD5 を二重引用符で囲む）。"""
    return '"' + hashlib.md5(data).hexdigest() + '"'  # noqa: S324  (ETag 用途・暗号目的でない)


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
