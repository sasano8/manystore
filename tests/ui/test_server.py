"""server 層のテスト（ASGI TestClient で in-process）。REST の CRUD と WS ライブ通知。"""

from pathlib import Path

from fastapi.testclient import TestClient

from manystore.implement.config import parse_config
from manystore.implement.service import StorageService
from manystore.server.app import create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = parse_config(
        {
            "contexts": {
                "work": {"backend": "local", "root": str(tmp_path / "work")},
                "ro": {"backend": "local", "root": str(tmp_path / "ro"), "writable": False},
            },
            "views": {"featured": [{"context": "work", "path": "interrupt", "pin": True}]},
            "default_context": "work",
        }
    )
    service = StorageService(cfg, watch_interval=0.05)
    return TestClient(create_app(service))


def test_rest_crud(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # contexts 一覧 + featured + default。
        meta = client.get("/contexts").json()
        assert {c["name"] for c in meta["contexts"]} == {"work", "ro"}
        assert meta["default_context"] == "work"
        assert meta["featured"][0]["context"] == "work"

        # PUT → GET → HEAD → list → DELETE。
        assert client.put("/contexts/work/objects/interrupt/n.md", content=b"hi").status_code == 204
        r = client.get("/contexts/work/objects/interrupt/n.md")
        assert r.status_code == 200 and r.content == b"hi"
        assert client.head("/contexts/work/objects/interrupt/n.md").status_code == 200

        keys = client.get("/contexts/work/keys", params={"prefix": "interrupt/"}).json()
        assert [e["key"] for e in keys["entries"]] == ["interrupt/n.md"]

        assert client.delete("/contexts/work/objects/interrupt/n.md").status_code == 204
        assert client.get("/contexts/work/objects/interrupt/n.md").status_code == 404


def test_readonly_and_unknown(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.put("/contexts/ro/objects/a.txt", content=b"x").status_code == 403
        assert client.get("/contexts/missing/keys").status_code == 404


def test_ws_live_notification(tmp_path: Path) -> None:
    with (
        _client(tmp_path) as client,
        client.websocket_connect("/contexts/work/events") as ws,
    ):
        client.put("/contexts/work/objects/live.txt", content=b"v")
        msg = ws.receive_json()
        assert msg["type"] == "created"
        assert msg["key"] == "live.txt"
        assert msg["context"] == "work"
