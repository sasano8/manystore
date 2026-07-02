"""名前 URL でストアを開く（M069）のテスト。

`parse_store_url` の (backend, opts) 分解と、`open_store` が接続済みストアを開くことを検証する。
"""

from pathlib import Path

import pytest

from manystore import open_store, parse_store_url
from manystore.storage.backends import (
    DictKeyValueStore,
    HttpKeyValueStore,
    LocalKeyValueStore,
    NatsObjectKeyValueStore,
    S3KeyValueStore,
)
from manystore.storage.surfaces.safe import SafeKeyValueStore


def test_parse_memory() -> None:
    assert parse_store_url("memory://") == ("memory", {})


def test_parse_local_variants() -> None:
    assert parse_store_url("local://.") == ("local", {"local_dir": Path(".")})
    assert parse_store_url("local:///abs/path") == ("local", {"local_dir": Path("/abs/path")})
    assert parse_store_url("local://./data") == ("local", {"local_dir": Path("./data")})
    # 空 netloc/path は cwd。
    assert parse_store_url("local://") == ("local", {"local_dir": Path(".")})


def test_parse_s3_netloc_bucket_and_query_opts() -> None:
    backend, opts = parse_store_url(
        "s3://mybucket?endpoint=http://localhost:9000&region=us-west-2"
        "&access_key=AK&secret_key=SK&addressing_style=path"
    )
    assert backend == "s3"
    assert opts == {
        "s3_bucket": "mybucket",
        "s3_endpoint": "http://localhost:9000",
        "s3_region": "us-west-2",
        "s3_access_key": "AK",
        "s3_secret_key": "SK",
        "s3_addressing_style": "path",
    }


def test_parse_s3_minimal_falls_back_to_defaults() -> None:
    # 資格情報未指定＝boto 既定チェーンに委ねる（opts に載せない）。
    assert parse_store_url("s3://bkt") == ("s3", {"s3_bucket": "bkt"})


def test_parse_nats_bucket_netloc_server_in_query() -> None:
    backend, opts = parse_store_url("nats://mybucket?server=nats://localhost:4222")
    assert backend == "nats"
    assert opts == {"nats_bucket": "mybucket", "nats_url": "nats://localhost:4222"}


def test_parse_http_is_whole_base_url() -> None:
    assert parse_store_url("http://host/base") == ("http", {"http_base_url": "http://host/base"})
    assert parse_store_url("https://h/x") == ("http", {"http_base_url": "https://h/x"})


def test_parse_manystore_context_and_server() -> None:
    backend, opts = parse_store_url("manystore://ctx?server=http://host/kv/raw")
    assert backend == "manystore"
    assert opts == {"context": "ctx", "base_url": "http://host/kv/raw"}


def test_parse_unknown_scheme_passes_through_to_registry() -> None:
    # plugin backend 名として scheme をそのまま・netloc=bucket・query を素通し。
    backend, opts = parse_store_url("foo://bkt?x=1")
    assert backend == "foo"
    assert opts == {"bucket": "bkt", "x": "1"}


def test_parse_requires_scheme() -> None:
    with pytest.raises(ValueError, match="requires a scheme"):
        parse_store_url("no-scheme")


async def test_open_store_memory_roundtrip() -> None:
    async with open_store("memory://") as store:
        assert isinstance(store, SafeKeyValueStore)  # Safe 包装されている
        await store.put("k", b"v")
        assert await store.get("k") == b"v"


async def test_open_store_local_roundtrip(tmp_path: Path) -> None:
    async with open_store(f"local://{tmp_path}") as store:
        await store.put("a/b.bin", b"data")
        assert await store.get("a/b.bin") == b"data"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("memory://", DictKeyValueStore),
        ("local://.", LocalKeyValueStore),
        ("s3://b?endpoint=http://x", S3KeyValueStore),
        ("nats://b?server=nats://x:4222", NatsObjectKeyValueStore),
        ("http://h/x", HttpKeyValueStore),
    ],
)
def test_url_builds_expected_backend_type(url: str, expected: type) -> None:
    # 接続はせず、URL→backend 型の対応だけを確認（registry 経由・未接続の生型を突き合わせ）。
    from manystore.storage.backends import create_unsafe_key_value_store

    backend, opts = parse_store_url(url)
    assert isinstance(create_unsafe_key_value_store(backend, **opts), expected)
