"""gateway 層のテスト（ASGI TestClient で in-process）。

S3 互換ルート（GET/PUT/HEAD/DELETE/ListObjectsV2）を local backend に対して検証する。
S3 XML は stdlib ElementTree で生成するので、レスポンスは XML としてパースして確認する。
"""

from pathlib import Path
from xml.etree.ElementTree import fromstring

from fastapi.testclient import TestClient

from manystore.serving.gateway.app import create_gateway
from manystore.serving.services.config import parse_config
from manystore.serving.services.s3map import S3_NS
from manystore.serving.services.service import StorageService

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


# ── S2: Multipart Upload の HTTP ルート分岐（in-process TestClient） ──


def test_multipart_create_upload_complete_get(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # CreateMultipartUpload: POST /{bucket}/{key}?uploads → uploadId 発行。
        r = client.post("/work/big.bin", params={"uploads": ""})
        assert r.status_code == 200
        upload_id = fromstring(r.content).findtext("s3:UploadId", namespaces=_NS)
        assert upload_id

        # UploadPart: PUT /{bucket}/{key}?partNumber=N&uploadId=X（part の ETag を返す）。
        for n, chunk in ((1, b"hello-"), (2, b"world")):
            r = client.put(
                "/work/big.bin",
                params={"partNumber": str(n), "uploadId": upload_id},
                content=chunk,
            )
            assert r.status_code == 200
            assert r.headers["ETag"].startswith('"')

        # CompleteMultipartUpload: POST /{bucket}/{key}?uploadId=X（Part 列を本文で指定）。
        complete_body = (
            "<CompleteMultipartUpload>"
            "<Part><PartNumber>1</PartNumber></Part>"
            "<Part><PartNumber>2</PartNumber></Part>"
            "</CompleteMultipartUpload>"
        )
        r = client.post("/work/big.bin", params={"uploadId": upload_id}, content=complete_body)
        assert r.status_code == 200
        etag = fromstring(r.content).findtext("s3:ETag", namespaces=_NS)
        assert etag.rstrip('"').endswith("-2")

        # 結合結果が GET で一致する。
        r = client.get("/work/big.bin")
        assert r.status_code == 200
        assert r.content == b"hello-world"

        # 一時 part は一覧に出ない。
        r = client.get("/work", params={"list-type": "2"})
        keys = {
            c.findtext("s3:Key", namespaces=_NS)
            for c in fromstring(r.content).findall("s3:Contents", _NS)
        }
        assert keys == {"big.bin"}


def test_multipart_abort_discards_parts(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post("/work/x.bin", params={"uploads": ""})
        upload_id = fromstring(r.content).findtext("s3:UploadId", namespaces=_NS)
        client.put(
            "/work/x.bin", params={"partNumber": "1", "uploadId": upload_id}, content=b"data"
        )

        # AbortMultipartUpload: DELETE /{bucket}/{key}?uploadId=X → 204。
        r = client.delete("/work/x.bin", params={"uploadId": upload_id})
        assert r.status_code == 204

        # 本オブジェクトは未作成・一時 part も掃除済み。
        assert client.get("/work/x.bin").status_code == 404
        r = client.get("/work", params={"list-type": "2"})
        assert fromstring(r.content).findall("s3:Contents", _NS) == []


def test_multipart_complete_missing_part_returns_nosuchupload(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post("/work/y.bin", params={"uploads": ""})
        upload_id = fromstring(r.content).findtext("s3:UploadId", namespaces=_NS)
        client.put(
            "/work/y.bin", params={"partNumber": "1", "uploadId": upload_id}, content=b"only-part-1"
        )
        # part 2 を要求するが未アップロード → NoSuchUpload(404)。
        complete_body = (
            "<CompleteMultipartUpload>"
            "<Part><PartNumber>1</PartNumber></Part>"
            "<Part><PartNumber>2</PartNumber></Part>"
            "</CompleteMultipartUpload>"
        )
        r = client.post("/work/y.bin", params={"uploadId": upload_id}, content=complete_body)
        assert r.status_code == 404
        assert fromstring(r.content).findtext("Code") == "NoSuchUpload"


def test_multipart_create_unknown_bucket_returns_nosuchbucket(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post("/missing/x.bin", params={"uploads": ""})
        assert r.status_code == 404
        assert fromstring(r.content).findtext("Code") == "NoSuchBucket"


def test_multipart_upload_part_invalid_part_number(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        r = client.post("/work/z.bin", params={"uploads": ""})
        upload_id = fromstring(r.content).findtext("s3:UploadId", namespaces=_NS)
        r = client.put(
            "/work/z.bin", params={"partNumber": "0", "uploadId": upload_id}, content=b"x"
        )
        assert r.status_code == 400
        assert fromstring(r.content).findtext("Code") == "InvalidArgument"


# ── conditional write（S3 conditional PUT・M046）＝If-None-Match: * / If-Match: <etag|*> ──


def test_put_if_none_match_star_create_only(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 1 回目＝不在なので作成され 200。2 回目＝既存ゆえ 412 PreconditionFailed。
        r = client.put("/work/c.txt", content=b"v1", headers={"If-None-Match": "*"})
        assert r.status_code == 200
        r = client.put("/work/c.txt", content=b"v2", headers={"If-None-Match": "*"})
        assert r.status_code == 412
        assert fromstring(r.content).findtext("Code") == "PreconditionFailed"
        assert client.get("/work/c.txt").content == b"v1"  # 敗者に上書きされない


def test_put_if_match_etag_update_cas(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put("/work/u.txt", content=b"v1")
        etag = client.head("/work/u.txt").headers["ETag"]  # 現 ETag（本体 MD5）
        # 一致する ETag での更新は 200。
        r = client.put("/work/u.txt", content=b"v2", headers={"If-Match": etag})
        assert r.status_code == 200
        assert client.get("/work/u.txt").content == b"v2"
        # 版が進んだので**古い** ETag での再更新は 412（lost-update を拒否）。
        r = client.put("/work/u.txt", content=b"v3", headers={"If-Match": etag})
        assert r.status_code == 412
        assert fromstring(r.content).findtext("Code") == "PreconditionFailed"
        assert client.get("/work/u.txt").content == b"v2"


def test_put_if_match_on_absent_is_precondition_failed(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 対象が無いのに If-Match＝precondition 不成立（412）。
        r = client.put("/work/none.txt", content=b"x", headers={"If-Match": '"deadbeef"'})
        assert r.status_code == 412
        assert fromstring(r.content).findtext("Code") == "PreconditionFailed"
        assert client.get("/work/none.txt").status_code == 404  # 書かれていない


def test_put_if_match_star_requires_existence(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # If-Match: * は「存在すれば更新」。不在なら 412、存在すれば 200。
        r = client.put("/work/s.txt", content=b"x", headers={"If-Match": "*"})
        assert r.status_code == 412
        client.put("/work/s.txt", content=b"v1")
        r = client.put("/work/s.txt", content=b"v2", headers={"If-Match": "*"})
        assert r.status_code == 200
        assert client.get("/work/s.txt").content == b"v2"


def test_put_if_none_match_non_star_is_not_implemented(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # S3 PutObject の If-None-Match は `*` のみ＝具体 etag は 501 NotImplemented（fail-loud）。
        r = client.put("/work/n.txt", content=b"x", headers={"If-None-Match": '"abc"'})
        assert r.status_code == 501
        assert fromstring(r.content).findtext("Code") == "NotImplemented"
