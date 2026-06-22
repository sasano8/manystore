"""routes — REST + WebSocket エンドポイント（protocol の実体）。

KeyValueStore と 1:1 で対応する薄い HTTP アダプタ:
- GET    /contexts                       … context 一覧 + featured + default_context
- GET    /contexts/{ctx}/keys            … キー一覧（?prefix= &limit=）
- HEAD   /contexts/{ctx}/objects/{key}   … 存在確認
- GET    /contexts/{ctx}/objects/{key}   … 取得（bytes）
- PUT    /contexts/{ctx}/objects/{key}   … 書き込み（body=bytes）
- DELETE /contexts/{ctx}/objects/{key}   … 削除
- WS     /contexts/{ctx}/events          … 変更イベントを push

interrupt 投入は「featured な local context への PUT」として、この汎用 PUT で成立する
（専用エンドポイントは持たない＝UI は汎用のまま）。
"""

from dataclasses import asdict

from ..implement.service import ContextNotFound, ReadOnlyContext, StorageService
from ..safe_path import UnsafePathError


def build_router(service: StorageService):
    """`service` を載せた manystore ネイティブ REST/WS ルートの [APIRouter] を返す。

    統合アプリは `app.include_router(build_router(service), prefix="/manystore")` で前置でき、
    単体アプリ（[create_app]）は prefix なしで include する。相対パス
    （`/contexts/...` 等）は prefix が前置されるだけで本体は不変。fastapi は遅延 import。
    """
    from fastapi import (
        APIRouter,
        HTTPException,
        Request,
        Response,
        WebSocket,
        WebSocketDisconnect,
    )

    app = APIRouter()

    def _http_error(exc: Exception) -> HTTPException:
        if isinstance(exc, ContextNotFound):
            return HTTPException(status_code=404, detail=f"unknown context: {exc}")
        if isinstance(exc, ReadOnlyContext):
            return HTTPException(status_code=403, detail=f"read-only context: {exc}")
        if isinstance(exc, UnsafePathError):
            return HTTPException(status_code=400, detail=str(exc))
        raise exc

    @app.get("/contexts")
    async def list_contexts() -> dict[str, object]:
        return {
            "contexts": [asdict(c) for c in service.list_contexts()],
            "featured": service.featured(),
            "default_context": service.default_context,
        }

    @app.get("/contexts/{context}/keys")
    async def list_keys(context: str, prefix: str = "", limit: int = 1000) -> dict[str, object]:
        try:
            entries = await service.list_entries(context, prefix=prefix, limit=limit)
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"entries": [asdict(e) for e in entries]}

    @app.head("/contexts/{context}/objects/{key:path}")
    async def head_object(context: str, key: str) -> Response:
        try:
            ok = await service.exists(context, key)
        except Exception as exc:
            raise _http_error(exc) from exc
        return Response(status_code=200 if ok else 404)

    @app.get("/contexts/{context}/objects/{key:path}")
    async def get_object(context: str, key: str) -> Response:
        try:
            data = await service.get(context, key)
        except Exception as exc:
            raise _http_error(exc) from exc
        if data is None:
            raise HTTPException(status_code=404, detail="not found")
        return Response(content=data, media_type="application/octet-stream")

    @app.put("/contexts/{context}/objects/{key:path}", status_code=204)
    async def put_object(context: str, key: str, request: Request) -> Response:
        body = await request.body()
        try:
            await service.put(context, key, body)
        except Exception as exc:
            raise _http_error(exc) from exc
        return Response(status_code=204)

    @app.delete("/contexts/{context}/objects/{key:path}", status_code=204)
    async def delete_object(context: str, key: str) -> Response:
        try:
            await service.delete(context, key)
        except Exception as exc:
            raise _http_error(exc) from exc
        return Response(status_code=204)

    @app.websocket("/contexts/{context}/events")
    async def events(ws: WebSocket, context: str) -> None:
        try:
            watcher = service.watcher(context)
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
    """`app`（FastAPI）に protocol のルートを登録する（後方互換の薄いシム）。

    内部で [build_router] が返す [APIRouter] を `app.include_router(...)` する。
    既存の単体アプリ生成（[create_app]）はこの形のまま動く。
    """
    app.include_router(build_router(service))
