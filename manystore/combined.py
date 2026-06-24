"""combined — 2 つのフロント（manystore ネイティブ REST と S3 互換）を 1 つの FastAPI
アプリに束ねる統合エントリポイント（M023・名前空間は M025 で再編）。

クライアントから見たパス第一階層を **バッファリング性**で分ける（M025）:

- `/kv/...`      → バッファする（値まるごと get/put・辞書オブジェクト的）系。
  - `/kv/raw/...` → manystore ネイティブ REST/WS（[server] 層・生バイト素通し）。
- `/storage/...` → ストリーミング（バッファしない・ファイルオープン的）系。
  - `/storage/s3/...` → S3 互換ゲートウェイ（[gateway] 層）。S3 クライアントは
    `endpoint_url=<host>/storage/s3` を向ければ `/storage/s3/{bucket}/{key}` に解決（path-style）。

設計（要点）:
- **`include_router(router, prefix=...)` で 1 アプリに束ねる**。`app.mount()` のサブアプリは
  Starlette で lifespan が走らない落とし穴があるため避ける（共有 service の connect が
  起動時に呼ばれなくなる）。各 routes 層は [APIRouter] を返す `build_router(service)` を
  提供しており、ここで prefix を付けて include する。
- **共有 [StorageService] を 1 回だけ connect する単一 lifespan**を統合アプリが持つ
  （二重 connect/aclose を避ける）。単体アプリ（[create_app] / [create_gateway]）はそれぞれ
  自前の lifespan を持つが、統合アプリでは両者を include するだけで lifespan は持ち込まない
  （router は lifespan を持たない）。
- fastapi は遅延 import（`manystore[server]` 未導入でも `import manystore` は壊さない）。

後方互換: 既存の単体アプリ（[manystore.server.create_app] / [manystore.gateway.create_gateway]）
と各 `__main__` はそのまま動く。本統合はそれらに**追加**するだけで、何も壊さない。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .gateway.routes import STORAGE_S3_PREFIX
from .gateway.routes import build_router as build_s3_router
from .implement.service import StorageService
from .server.routes import KV_RAW_PREFIX
from .server.routes import build_router as build_native_router


def create_combined_app(service: StorageService):
    """`service` を共有する統合 FastAPI アプリを返す。

    `/kv/raw` に manystore ネイティブ REST/WS（buffered）、`/storage/s3` に S3 互換
    ゲートウェイ（streaming）を include する。アプリのライフサイクルで共有 `service` を
    1 回だけ `connect()` / `aclose()` する。
    """
    from fastapi import FastAPI

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 共有 service を統合アプリで 1 回だけ接続/切断する（二重 connect/aclose を避ける）。
        await service.connect()
        try:
            yield
        finally:
            await service.aclose()

    app = FastAPI(title="manystore combined (REST + S3)", lifespan=lifespan)
    # APIRouter を prefix 付きで include（mount ではない＝lifespan は統合アプリが一本化）。
    # 第1階層は buffer 性で分ける: /kv=バッファ系・/storage=ストリーミング系（M025）。
    app.include_router(build_native_router(service), prefix=KV_RAW_PREFIX)
    app.include_router(build_s3_router(service), prefix=STORAGE_S3_PREFIX)
    return app
