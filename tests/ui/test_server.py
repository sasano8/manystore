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


def test_error_responses_are_problem_json(tmp_path: Path) -> None:
    # エラーは application/problem+json（RFC 9457）で返す。status は本文にも入る。
    with _client(tmp_path) as client:
        ro = client.put("/contexts/ro/objects/a.txt", content=b"x")
        assert ro.status_code == 403
        assert ro.headers["content-type"].startswith("application/problem+json")
        body = ro.json()
        assert body["status"] == 403 and body["title"] == "Read-Only Context"

        # 不明 context は 404 problem。
        nf = client.get("/contexts/missing/keys")
        assert nf.status_code == 404
        assert nf.headers["content-type"].startswith("application/problem+json")
        assert nf.json()["title"] == "Context Not Found"

        # 不正キー（パストラバーサル）は 400 problem。%2e%2e で `..` を素通しさせる
        # （生の `../` は URL 正規化で畳まれてルートに届かないため）。
        bad = client.get("/contexts/work/objects/%2e%2e/escape")
        assert bad.status_code == 400
        assert bad.json()["title"] == "Unsafe Path"

        # 欠損キーの GET は 404 problem。
        miss = client.get("/contexts/work/objects/nope.txt")
        assert miss.status_code == 404
        assert miss.headers["content-type"].startswith("application/problem+json")


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
