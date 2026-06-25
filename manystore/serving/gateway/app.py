"""app — S3 互換ゲートウェイの FastAPI アプリのファクトリ。

[StorageService] を受け取り、ライフサイクル（起動で connect / 終了で aclose）を結び、
S3 互換 REST ルートを載せた FastAPI アプリを返す。M019（server 層）と同型で、fastapi は
遅延 import（`manystore[server]` extra 未導入でも `import manystore` は壊さない）。

server 層との違いは「前段の protocol」だけ＝manystore REST → S3 XML/REST。サービス中核
（[StorageService]）は再利用し、2 度書かない。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..services.service import StorageService
from .routes import register_routes


def create_gateway(service: StorageService):
    """`service` を載せた S3 互換ゲートウェイの FastAPI アプリを返す。

    アプリのライフサイクルで `service.connect()` / `service.aclose()` を呼ぶので、
    ASGI ランタイム（uvicorn / TestClient）が起動・終了に合わせて接続を張る。
    """
    from fastapi import FastAPI

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.connect()
        try:
            yield
        finally:
            await service.aclose()

    app = FastAPI(title="manystore S3 gateway", lifespan=lifespan)
    register_routes(app, service)
    return app
