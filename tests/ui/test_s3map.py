"""s3map の純ロジック（HTTP 非依存）テスト: delimiter 畳み込みと XML 生成。"""

from xml.etree.ElementTree import fromstring

from manystore.serving.services.protocol import EntryInfo
from manystore.serving.services.s3map import (
    S3_NS,
    fold_list_v2,
    parse_complete_multipart,
    render_complete_multipart,
    render_error,
    render_initiate_multipart,
    render_list_v2,
)

_NS = {"s3": S3_NS}


def _e(key: str, size: int = 1) -> EntryInfo:
    return EntryInfo(key=key, size=size)


def test_fold_no_delimiter_returns_all_as_contents() -> None:
    entries = [_e("a.txt"), _e("b/c.txt")]
    contents, common = fold_list_v2(entries, prefix="", delimiter="")
    assert contents == entries
    assert common == []


def test_fold_delimiter_groups_common_prefixes() -> None:
    entries = [_e("top.txt"), _e("dir1/x"), _e("dir1/y"), _e("dir2/z")]
    contents, common = fold_list_v2(entries, prefix="", delimiter="/")
    assert [c.key for c in contents] == ["top.txt"]
    assert common == ["dir1/", "dir2/"]


def test_fold_with_prefix_strips_then_groups() -> None:
    entries = [_e("dir1/a.txt"), _e("dir1/sub/b.txt")]
    contents, common = fold_list_v2(entries, prefix="dir1/", delimiter="/")
    assert [c.key for c in contents] == ["dir1/a.txt"]
    assert common == ["dir1/sub/"]


def test_render_list_v2_has_namespace_and_counts() -> None:
    body = render_list_v2(
        bucket="work",
        prefix="dir1/",
        delimiter="/",
        contents=[_e("dir1/a.txt", 5)],
        common_prefixes=["dir1/sub/"],
        max_keys=1000,
        is_truncated=False,
    )
    root = fromstring(body)
    assert root.tag == f"{{{S3_NS}}}ListBucketResult"
    assert root.findtext("s3:Name", namespaces=_NS) == "work"
    assert root.findtext("s3:KeyCount", namespaces=_NS) == "2"
    assert root.findtext("s3:IsTruncated", namespaces=_NS) == "false"
    assert root.findtext("s3:Contents/s3:Size", namespaces=_NS) == "5"
    assert root.findtext("s3:CommonPrefixes/s3:Prefix", namespaces=_NS) == "dir1/sub/"


def test_render_error_shape() -> None:
    root = fromstring(render_error(code="NoSuchKey", message="missing", resource="/work/x"))
    assert root.tag == "Error"
    assert root.findtext("Code") == "NoSuchKey"
    assert root.findtext("Message") == "missing"
    assert root.findtext("Resource") == "/work/x"


# ── multipart（S2）の XML 補助 ──


def test_render_initiate_multipart_carries_upload_id() -> None:
    root = fromstring(render_initiate_multipart(bucket="work", key="big.bin", upload_id="u123"))
    assert root.tag == f"{{{S3_NS}}}InitiateMultipartUploadResult"
    assert root.findtext("s3:Bucket", namespaces=_NS) == "work"
    assert root.findtext("s3:Key", namespaces=_NS) == "big.bin"
    assert root.findtext("s3:UploadId", namespaces=_NS) == "u123"


def test_render_complete_multipart_carries_etag() -> None:
    root = fromstring(render_complete_multipart(bucket="work", key="big.bin", etag='"abc-3"'))
    assert root.tag == f"{{{S3_NS}}}CompleteMultipartUploadResult"
    assert root.findtext("s3:Key", namespaces=_NS) == "big.bin"
    assert root.findtext("s3:ETag", namespaces=_NS) == '"abc-3"'


def test_parse_complete_multipart_preserves_request_order() -> None:
    # クライアントが送る Part 列の順序をそのまま返す（サーバは再ソートしない）。
    body = (
        b'<CompleteMultipartUpload xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<Part><PartNumber>1</PartNumber><ETag>&quot;a&quot;</ETag></Part>"
        b"<Part><PartNumber>2</PartNumber><ETag>&quot;b&quot;</ETag></Part>"
        b"<Part><PartNumber>3</PartNumber><ETag>&quot;c&quot;</ETag></Part>"
        b"</CompleteMultipartUpload>"
    )
    assert parse_complete_multipart(body) == [1, 2, 3]


def test_parse_complete_multipart_without_namespace() -> None:
    # 名前空間なしの本文（ローカル名で判定）でも part を拾える。
    body = (
        b"<CompleteMultipartUpload>"
        b"<Part><PartNumber>5</PartNumber></Part>"
        b"<Part><PartNumber>7</PartNumber></Part>"
        b"</CompleteMultipartUpload>"
    )
    assert parse_complete_multipart(body) == [5, 7]
