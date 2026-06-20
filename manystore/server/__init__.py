"""server — manystore の context 群を HTTP+WS で公開するサーバ層（汎用ストレージ UI）。

`manystore[server]` extra（fastapi / uvicorn / watchdog）が要る。fastapi 等はここで遅延 import
するので、未インストールでも本体（`import manystore`）は壊れない。

- [create_app] … [StorageService] を載せた FastAPI アプリを返す（`from .app import create_app`）。
- `python -m manystore.server --config <toml>` … uvicorn で起動する（[__main__]）。
"""

from .app import create_app

__all__ = ["create_app"]
