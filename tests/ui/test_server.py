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
        # bucket 一覧 + featured + default（NS ルート = GET /kv/raw/）。
        meta = client.get("/kv/raw/").json()
        assert {c["name"] for c in meta["contexts"]} == {"work", "ro"}
        assert meta["default_context"] == "work"
        assert meta["featured"][0]["context"] == "work"

        # PUT → GET → HEAD → list → DELETE（addressing = {bucket}/{path}）。
        assert client.put("/kv/raw/work/interrupt/n.md", content=b"hi").status_code == 204
        r = client.get("/kv/raw/work/interrupt/n.md")
        assert r.status_code == 200 and r.content == b"hi"
        assert client.head("/kv/raw/work/interrupt/n.md").status_code == 200

        # 一覧はフラット list-all（prefix 撤去・subtree 絞りはクライアント側）。
        keys = client.get("/kv/raw/work/").json()
        assert [e["key"] for e in keys["entries"]] == ["interrupt/n.md"]

        assert client.delete("/kv/raw/work/interrupt/n.md").status_code == 204
        assert client.get("/kv/raw/work/interrupt/n.md").status_code == 404


def test_readonly_and_unknown(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.put("/kv/raw/ro/a.txt", content=b"x").status_code == 403
        assert client.get("/kv/raw/missing/").status_code == 404


def test_error_responses_are_problem_json(tmp_path: Path) -> None:
    # エラーは application/problem+json（RFC 9457）で返す。status は本文にも入る。
    with _client(tmp_path) as client:
        ro = client.put("/kv/raw/ro/a.txt", content=b"x")
        assert ro.status_code == 403
        assert ro.headers["content-type"].startswith("application/problem+json")
        body = ro.json()
        assert body["status"] == 403 and body["title"] == "Read-Only Context"

        # 不明 bucket は 404 problem。
        nf = client.get("/kv/raw/missing/")
        assert nf.status_code == 404
        assert nf.headers["content-type"].startswith("application/problem+json")
        assert nf.json()["title"] == "Context Not Found"

        # 不正キー（パストラバーサル）は 400 problem。%2e%2e で `..` を素通しさせる
        # （生の `../` は URL 正規化で畳まれてルートに届かないため）。
        bad = client.get("/kv/raw/work/%2e%2e/escape")
        assert bad.status_code == 400
        assert bad.json()["title"] == "Unsafe Path"

        # 欠損キーの GET は 404 problem。
        miss = client.get("/kv/raw/work/nope.txt")
        assert miss.status_code == 404
        assert miss.headers["content-type"].startswith("application/problem+json")


def test_ws_live_notification(tmp_path: Path) -> None:
    with (
        _client(tmp_path) as client,
        client.websocket_connect("/kv/raw/work/") as ws,
    ):
        client.put("/kv/raw/work/live.txt", content=b"v")
        msg = ws.receive_json()
        assert msg["type"] == "created"
        assert msg["key"] == "live.txt"
        assert msg["context"] == "work"
