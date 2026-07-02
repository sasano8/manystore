"""s3map — S3 protocol ⇄ Store のマッピング補助（HTTP 非依存）。

gateway 層（FastAPI）から切り離した純ロジックを置く＝HTTP を立てずに単体テストできる:

- ListObjectsV2 の `delimiter` 畳み込み（フラットなキー列 → Contents + CommonPrefixes）。
- S3 互換 XML レスポンスの生成（stdlib `xml.etree.ElementTree` のみ＝新依存ゼロ）。
- S3 エラー XML の生成。

bucket = manystore の context。XML 名前空間は AWS S3 互換クライアント（boto3 / aws-cli）が
期待する `http://s3.amazonaws.com/doc/2006-03-01/` を使う。
"""

from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

from .protocol import EntryInfo

# S3 互換クライアントが期待する XML 名前空間。
S3_NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def fold_list_v2(
    entries: list[EntryInfo], prefix: str, delimiter: str
) -> tuple[list[EntryInfo], list[str]]:
    """ListObjectsV2 の delimiter 畳み込み。

    `entries` は `service.list_entries(context, prefix=...)` の結果（prefix で前方一致済み）。
    delimiter が空なら全件を Contents として返す（CommonPrefixes 無し）。
    delimiter があれば、prefix を除いた残りに delimiter が出現するキーを
    「最初の delimiter までの共通プレフィクス」へ畳み、CommonPrefixes として返す。
    残り（delimiter を含まないキー）は Contents としてそのまま返す。

    返り値: (contents, common_prefixes)。common_prefixes は重複排除しソート済み。
    """
    if not delimiter:
        return list(entries), []

    contents: list[EntryInfo] = []
    common: dict[str, None] = {}  # 挿入順を保つ集合代わり
    for e in entries:
        rest = e.key[len(prefix) :] if prefix else e.key
        idx = rest.find(delimiter)
        if idx == -1:
            contents.append(e)
        else:
            cp = (prefix or "") + rest[: idx + len(delimiter)]
            common[cp] = None
    return contents, sorted(common)


def render_list_v2(
    *,
    bucket: str,
    prefix: str,
    delimiter: str,
    contents: list[EntryInfo],
    common_prefixes: list[str],
    max_keys: int,
    is_truncated: bool,
) -> bytes:
    """ListObjectsV2 の成功レスポンス XML（bytes・UTF-8）を生成する。"""
    root = Element("ListBucketResult", xmlns=S3_NS)
    _text(root, "Name", bucket)
    _text(root, "Prefix", prefix)
    if delimiter:
        _text(root, "Delimiter", delimiter)
    _text(root, "KeyCount", str(len(contents) + len(common_prefixes)))
    _text(root, "MaxKeys", str(max_keys))
    _text(root, "IsTruncated", "true" if is_truncated else "false")
    for e in contents:
        c = SubElement(root, "Contents")
        _text(c, "Key", e.key)
        _text(c, "Size", str(e.size))
    for cp in common_prefixes:
        node = SubElement(root, "CommonPrefixes")
        _text(node, "Prefix", cp)
    return _serialize(root)


def render_error(*, code: str, message: str, resource: str = "") -> bytes:
    """S3 エラーレスポンス XML（bytes・UTF-8）を生成する。"""
    root = Element("Error")
    _text(root, "Code", code)
    _text(root, "Message", message)
    if resource:
        _text(root, "Resource", resource)
    return _serialize(root)


# ── Multipart Upload（S2）の XML 補助 ──


def render_initiate_multipart(*, bucket: str, key: str, upload_id: str) -> bytes:
    """CreateMultipartUpload の成功レスポンス XML（uploadId を発行）を生成する。"""
    root = Element("InitiateMultipartUploadResult", xmlns=S3_NS)
    _text(root, "Bucket", bucket)
    _text(root, "Key", key)
    _text(root, "UploadId", upload_id)
    return _serialize(root)


def render_complete_multipart(*, bucket: str, key: str, etag: str, location: str = "") -> bytes:
    """CompleteMultipartUpload の成功レスポンス XML（最終 ETag）を生成する。"""
    root = Element("CompleteMultipartUploadResult", xmlns=S3_NS)
    _text(root, "Location", location or f"/{bucket}/{key}")
    _text(root, "Bucket", bucket)
    _text(root, "Key", key)
    _text(root, "ETag", etag)
    return _serialize(root)


def parse_complete_multipart(body: bytes) -> list[int]:
    """CompleteMultipartUpload のリクエスト XML から partNumber を出現順に取り出す。

    S3 クライアントは結合してほしい part を `<Part><PartNumber>N</PartNumber>...</Part>`
    の列で送る。本実装は **このリクエスト本文に並んだ順** で part を結合する（クライアントが
    昇順で送る規約に委ねる＝サーバ側で再ソートしない。順序はクライアント責務）。

    XML 名前空間の有無いずれにも耐えるよう、タグ名は末尾（`}` 以降）で判定する。
    返り値: PartNumber の int 列（重複・欠落の検証は呼び出し側が parts 実在で行う）。
    """
    root = fromstring(body)
    out: list[int] = []
    for part in root:
        if _localname(part.tag) != "Part":
            continue
        for child in part:
            if _localname(child.tag) == "PartNumber":
                out.append(int((child.text or "").strip()))
                break
    return out


def _localname(tag: str) -> str:
    """`{ns}Tag` 形式から名前空間を外したローカル名を返す。"""
    return tag.rsplit("}", 1)[-1]


def _text(parent: Element, tag: str, value: str) -> None:
    SubElement(parent, tag).text = value


def _serialize(root: Element) -> bytes:
    # tostring(default) は XML 宣言を付けないので自前で前置する（encoding を渡すと
    # ElementTree が小文字 "utf-8" の宣言を付けてしまうため避ける）。
    return b'<?xml version="1.0" encoding="UTF-8"?>' + tostring(root)
