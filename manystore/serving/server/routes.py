"""routes — REST + WebSocket エンドポイント（protocol の実体）。

addressing は **`{bucket}/{path}`**（M025改）。bucket は [ArrayStore] の第一階層
（mount）、後続は不透明な `path`。`contexts`/`objects`/`keys` の飾りは廃止し、表層語を
**bucket** に統一する（S3 と揃える。内部 [StorageService] の `context` 命名はそのまま）:

- GET    /                  … bucket(=mount) 一覧 + featured + default_context
- GET    /{bucket}/         … bucket 内の全キー（フラット・?limit=）
- WS     /{bucket}/         … 変更イベントを push（同パスを WS upgrade で判別）
- HEAD   /{bucket}/{path}   … 存在確認
- GET    /{bucket}/{path}   … 取得（bytes）
- PUT    /{bucket}/{path}   … 書き込み（body=bytes）
- DELETE /{bucket}/{path}   … 削除

コレクション判別＝**空パス=一覧 / 非空パス=オブジェクト**（prefix 撤去で末尾スラッシュ規則は
不要・曖昧さが消える）。prefix（仮想フォルダ）は native API から撤去し、backend/ラッパーの
optional capability へ移す（M030）。`{bucket}/` のキー一覧は**フラット**（subtree 絞りは
クライアント側で畳む）。`{path}` は不透明（サーバは階層解釈しない）。

interrupt 投入は「featured な local bucket への PUT」として、この汎用 PUT で成立する
（専用エンドポイントは持たない＝UI は汎用のまま）。

ルート登録順に注意：`GET /{bucket}/`（キー一覧）を `GET /{bucket}/{path:path}`（オブジェクト・
`{path}` は空文字も拾える）より**先**に登録し、`/{bucket}/` がオブジェクト経路に吸われないように。

エラー応答は **`application/problem+json`**（RFC 9457）で返す（[to_problem]）。ドメイン例外
（[ManystoreError]）は status/title 付き problem に、欠損は 404 problem に写す。想定外の例外は
握りつぶさず再送出（＝本物の 500）。S3 ゲートウェイは S3 互換 XML を返すため別系統（不採用）。
"""

from dataclasses import asdict

from ...spec import DEFAULT_LIST_LIMIT, FileInfo, IfMatch
from ...spec.exceptions import PROBLEM_JSON, ContextNotFound, ManystoreError, to_problem
from ..services.service import StorageService

# conditional put / head のメタを HTTP ヘッダに写す（M046・案B「HTTP 越し conformance」）。
#   - ETag: 下層 backend の CAS トークン（S3=ETag/local=mtime_ns-size/dict=世代）を quote で載せる。
#   - X-Manystore-Size/-Modified-At: size/modified_at（標準 HTTP-date は秒精度で lossy ゆえ独自）。
# client(remote) はこれを読んで head()->FileInfo を組み put(if_match=...) の条件ヘッダに使う:
#   省略→ヘッダ無し(LWW)／不在 FileInfo→`If-None-Match: *`(create-only)／FileInfo(etag)→
#   `If-Match: "<etag>"`(update CAS)。不一致は backend が ConflictError→_on_error が problem(409)。
_SIZE_HEADER = "X-Manystore-Size"
_MODIFIED_AT_HEADER = "X-Manystore-Modified-At"
_SHA256_HEADER = "X-Manystore-Sha256"


def _meta_headers(info: FileInfo) -> dict[str, str]:
    """[FileInfo] を HEAD 応答のメタヘッダ（ETag/size/modified_at/sha256）に写す（None は省く）。"""
    headers: dict[str, str] = {}
    etag = info.get("etag")
    if etag is not None:
        headers["ETag"] = f'"{etag}"'  # HTTP の ETag は quote 文字列
    size = info.get("size")
    if size is not None:
        headers[_SIZE_HEADER] = str(size)
    modified_at = info.get("modified_at")
    if modified_at is not None:
        headers[_MODIFIED_AT_HEADER] = repr(modified_at)  # float の往復精度を保つ
    sha256 = info.get("sha256")
    if sha256 is not None:
        headers[_SHA256_HEADER] = sha256  # 内容ハッシュ（M013・client の download 検証メタ）
    return headers


def _parse_if_match(headers, path: str) -> IfMatch:
    """条件ヘッダを [IfMatch] に解く（無し=None／`If-None-Match: *`=不在／`If-Match`=CAS）。"""
    if (headers.get("if-none-match") or "").strip() == "*":
        return FileInfo.absent(path)  # create-only（不在を要求）
    if_match = headers.get("if-match")
    if if_match is not None:
        etag = if_match.strip().strip('"')
        # size=0 は「不在でない」標識（is_absent は size=None のみ）。backend は etag だけで突合。
        return FileInfo(filename=path, size=0, etag=etag)
    return None


# native REST/WS を application に include する際の NS prefix（M025・単一正本）。
# combined / 単体 server（app.py）/ client（base_url）はこの 1 定数を参照し、prefix を散らさない
# （付け替えはここだけ・ベタ書きの drift を防ぐ）。NS=bucket 一覧は `GET {KV_RAW_PREFIX}/`。
KV_RAW_PREFIX = "/kv/raw"


def build_router(service: StorageService):
    """`service` を載せた manystore ネイティブ REST/WS ルートの [APIRouter] を返す。

    統合アプリ・単体アプリとも `app.include_router(build_router(service), prefix=KV_RAW_PREFIX)`
    で前置する（NS=`/kv/raw`）。相対パス（`/{bucket}/...` 等）は prefix が前置されるだけで
    本体は不変。fastapi は遅延 import。
    """
    from fastapi import (
        APIRouter,
        Request,
        Response,
        WebSocket,
        WebSocketDisconnect,
    )
    from fastapi.responses import JSONResponse

    app = APIRouter()

    def _problem(exc: Exception) -> JSONResponse:
        """例外を `application/problem+json` の [JSONResponse] に変換する。"""
        problem = to_problem(exc)
        return JSONResponse(problem, status_code=problem["status"], media_type=PROBLEM_JSON)

    def _on_error(exc: Exception) -> JSONResponse:
        """ドメイン例外は problem へ、想定外は再送出（握りつぶさず本物の 500 にする）。"""
        if not isinstance(exc, ManystoreError):
            raise exc
        return _problem(exc)

    @app.get("/")
    async def list_buckets() -> dict[str, object]:
        return {
            "contexts": [asdict(c) for c in service.list_contexts()],
            "featured": service.featured(),
            "default_context": service.default_context,
        }

    @app.get("/{bucket}/")
    async def list_keys(bucket: str, limit: int = DEFAULT_LIST_LIMIT):
        try:
            entries = await service.list_entries(bucket, limit=limit)
        except Exception as exc:
            return _on_error(exc)
        return {"entries": [asdict(e) for e in entries]}

    @app.head("/{bucket}/{path:path}")
    async def head_object(bucket: str, path: str) -> Response:
        try:
            info = await service.head_or_absent(bucket, path)
        except Exception as exc:
            return _on_error(exc)
        if info.is_absent():
            return Response(status_code=404)
        # 存在＝200＋メタ（ETag/size/modified_at）。client はここから version を読む。
        return Response(status_code=200, headers=_meta_headers(info))

    @app.get("/{bucket}/{path:path}")
    async def get_object(bucket: str, path: str) -> Response:
        try:
            data = await service.get(bucket, path)
        except Exception as exc:
            return _on_error(exc)
        if data is None:
            return _problem(FileNotFoundError("not found"))  # 404 problem
        return Response(content=data, media_type="application/octet-stream")

    @app.put("/{bucket}/{path:path}", status_code=204)
    async def put_object(bucket: str, path: str, request: Request) -> Response:
        body = await request.body()
        if_match = _parse_if_match(request.headers, path)  # conditional put（CAS）の条件
        try:
            await service.put(bucket, path, body, if_match=if_match)
        except Exception as exc:
            return _on_error(exc)  # ConflictError は problem(409) へ＝条件不一致を fail-loud
        return Response(status_code=204)

    @app.delete("/{bucket}/{path:path}", status_code=204)
    async def delete_object(bucket: str, path: str) -> Response:
        try:
            await service.delete(bucket, path)
        except Exception as exc:
            return _on_error(exc)
        return Response(status_code=204)

    @app.websocket("/{bucket}/")
    async def events(ws: WebSocket, bucket: str) -> None:
        try:
            watcher = service.watcher(bucket)
        except ContextNotFound:
            await ws.close(code=4404)
            return
        await ws.accept()
        try:
            async for ev in watcher.subscribe():
                await ws.send_json(asdict(ev))
        except WebSocketDisconnect:
            pass

    return app


def register_routes(app, service: StorageService) -> None:
    """`app`（FastAPI）に protocol のルートを root 直下で登録する（後方互換の薄いシム）。

    内部で [build_router] が返す [APIRouter] を prefix 無しで `app.include_router(...)` する。
    現行の [create_app] は NS=`/kv/raw` 前置で include するため本シムは使わないが、root 直下に
    native ルートを載せたい外部呼び出し向けに残す（bucket 一覧が `GET /` になる点に注意）。
    """
    app.include_router(build_router(service))
