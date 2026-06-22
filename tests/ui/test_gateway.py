"""gateway 層のテスト（ASGI TestClient で in-process）。

S3 互換ルート（GET/PUT/HEAD/DELETE/ListObjectsV2）を local backend に対して検証する。
S3 XML は stdlib ElementTree で生成するので、レスポンスは XML としてパースして確認する。
"""

from pathlib import Path
from xml.etree.ElementTree import fromstring

from fastapi.testclient import TestClient

from manystore.gateway.app import create_gateway
from manystore.implement.config import parse_config
from manystore.implement.s3map import S3_NS
from manystore.implement.service import StorageService

_NS = {"s3": S3_NS}


def _client(tmp_path: Path) -> TestClient:
    cfg = parse_config(
        {
            "contexts": {
                "work": {"backend": "local", "root": str(tmp_path / "work")},
                "ro": {"backend": "local", "root": str(tmp_path / "ro"), "writable": False},
            },
            "default_context": "work",
        }
    )
    service = StorageService(cfg, watch_interval=0.05)
    return TestClient(create_gateway(service))


def test_put_get_head_delete(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # PutObject → 200 + ETag。
        r = client.put("/work/dir/a.txt", content=b"hello")
        assert r.status_code == 200
        assert r.headers["ETag"].startswith('"') and r.headers["ETag"].endswith('"')

        # GetObject → 200 + body + ETag。
        r = client.get("/work/dir/a.txt")
        assert r.status_code == 200
        assert r.content == b"hello"
        assert "ETag" in r.headers

        # HeadObject → 200 + Content-Length。
        r = client.head("/work/dir/a.txt")
        assert r.status_code == 200
        assert r.headers["Content-Length"] == "5"

        # DeleteObject → 204。
        assert client.delete("/work/dir/a.txt").status_code == 204

        # 削除後の GET/HEAD → 404。
        assert client.get("/work/dir/a.txt").status_code == 404
        assert client.head("/work/dir/a.txt").status_code == 404


def test_get_missing_returns_s3_error_xml(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/work/nope.txt")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/xml")
        root = fromstring(r.content)
        assert root.tag == "Error"
        assert root.findtext("Code") == "NoSuchKey"


def test_unknown_bucket_returns_nosuchbucket(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.get("/missing/x.txt")
        assert r.status_code == 404
        assert fromstring(r.content).findtext("Code") == "NoSuchBucket"


def test_readonly_bucket_access_denied(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.put("/ro/x.txt", content=b"x")
        assert r.status_code == 403
        assert fromstring(r.content).findtext("Code") == "AccessDenied"


def test_unsafe_key_invalid_argument(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # バックスラッシュを含むキーは SafeKeyValueStore が弾く → S3 InvalidArgument(400)。
        # （'..' は HTTP クライアントがパス正規化で潰すため、正規化されない '\\' で検証する。）
        r = client.put("/work/a%5Cb.txt", content=b"x")
        assert r.status_code == 400
        assert fromstring(r.content).findtext("Code") == "InvalidArgument"


def test_list_objects_v2_flat(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put("/work/a.txt", content=b"1")
        client.put("/work/b/c.txt", content=b"22")

        r = client.get("/work", params={"list-type": "2"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/xml")
        root = fromstring(r.content)
        assert root.tag == f"{{{S3_NS}}}ListBucketResult"
        keys = {c.findtext("s3:Key", namespaces=_NS) for c in root.findall("s3:Contents", _NS)}
        assert keys == {"a.txt", "b/c.txt"}
        assert root.findall("s3:CommonPrefixes", _NS) == []


def test_list_objects_v2_delimiter_folds_common_prefixes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put("/work/top.txt", content=b"1")
        client.put("/work/dir1/x.txt", content=b"2")
        client.put("/work/dir1/y.txt", content=b"3")
        client.put("/work/dir2/z.txt", content=b"4")

        r = client.get("/work", params={"list-type": "2", "delimiter": "/"})
        assert r.status_code == 200
        root = fromstring(r.content)
        contents = {c.findtext("s3:Key", namespaces=_NS) for c in root.findall("s3:Contents", _NS)}
        prefixes = {
            p.findtext("s3:Prefix", namespaces=_NS) for p in root.findall("s3:CommonPrefixes", _NS)
        }
        assert contents == {"top.txt"}
        assert prefixes == {"dir1/", "dir2/"}


def test_list_objects_v2_prefix_and_delimiter(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put("/work/dir1/a.txt", content=b"1")
        client.put("/work/dir1/sub/b.txt", content=b"2")

        r = client.get("/work", params={"list-type": "2", "prefix": "dir1/", "delimiter": "/"})
        root = fromstring(r.content)
        contents = {c.findtext("s3:Key", namespaces=_NS) for c in root.findall("s3:Contents", _NS)}
        prefixes = {
            p.findtext("s3:Prefix", namespaces=_NS) for p in root.findall("s3:CommonPrefixes", _NS)
        }
        assert contents == {"dir1/a.txt"}
        assert prefixes == {"dir1/sub/"}
