"""s3map の純ロジック（HTTP 非依存）テスト: delimiter 畳み込みと XML 生成。"""

from xml.etree.ElementTree import fromstring

from manystore.implement.protocol import EntryInfo
from manystore.implement.s3map import (
    S3_NS,
    fold_list_v2,
    render_error,
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
