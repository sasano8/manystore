"""app — FastAPI アプリのファクトリ。

[StorageService] を受け取り、ライフサイクル（起動で connect / 終了で aclose）を結び、
REST/WS ルートと同梱フロントエンド（static/）を載せた FastAPI アプリを返す。
fastapi は遅延 import（`manystore[server]` 未導入でも `import manystore` は壊さない）。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ..implement.service import StorageService
from .routes import build_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(service: StorageService):
    """`service` を載せた FastAPI アプリを返す。

    アプリのライフサイクルで `service.connect()` / `service.aclose()` を呼ぶので、
    ASGI ランタイム（uvicorn / TestClient）が起動・終了に合わせて接続を張る。
    """
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.connect()
        try:
            yield
        finally:
            await service.aclose()

    app = FastAPI(title="manystore storage UI", lifespan=lifespan)
    # native REST/WS は NS=`/kv/raw` 配下（M025改・combined と一貫）。bucket 一覧が
    # `GET /kv/raw/` になるので、`/` を同梱フロントエンドの StaticFiles に明け渡せる。
    app.include_router(build_router(service), prefix="/kv/raw")

    if STATIC_DIR.is_dir():
        # 同梱フロントエンド（ビルドレス Web UI）。`/` で index.html を返す。
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")

    return app
