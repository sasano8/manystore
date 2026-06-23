"""横断的な準拠テスト（メソッド存在チェック）。

全 backend が `KeyValueStore` / `FileStore` Protocol のメソッドを揃えているかを 1 か所で確認する。
挙動の一致（契約スイート）は未実装＝ここは「前提メソッドが在るか」だけを見る。サードパーティ
backend も `manystore.conformance` の assert_key_value_store / assert_file_store を import すれば
同じ検査を回せる。
"""

import asyncio

import pytest

from manystore import (
    DictFileStore,
    DictKeyValueStore,
    HttpFileStore,
    HttpKeyValueStore,
    LocalFileStore,
    LocalKeyValueStore,
    NatsFileStore,
    NatsObjectKeyValueStore,
    S3FileStore,
    S3KeyValueStore,
)
from manystore.conformance import (
    assert_file_store,
    assert_key_value_store,
    check_file_store_contract,
    check_key_value_store_contract,
    missing_members,
    required_members,
)
from manystore.kv import KeyValueStore


def _kvs_instances(tmp_path):
    # 接続はしない（メソッド存在チェックは生成だけで十分）。
    return [
        DictKeyValueStore(),
        LocalKeyValueStore(tmp_path),
        S3KeyValueStore(bucket="b"),
        NatsObjectKeyValueStore(url="nats://x", bucket="b"),
        HttpKeyValueStore(base_url="http://x"),
    ]


def _file_store_instances(tmp_path):
    return [
        DictFileStore(),
        LocalFileStore(tmp_path),
        S3FileStore(bucket="b"),
        NatsFileStore(url="nats://x", bucket="b"),
        HttpFileStore(base_url="http://x"),
    ]


def test_all_key_value_stores_have_required_methods(tmp_path) -> None:
    for store in _kvs_instances(tmp_path):
        assert_key_value_store(store)  # 欠けていれば AssertionError で backend 名つき


def test_all_file_stores_have_required_methods(tmp_path) -> None:
    # FileStore は KVS + open_reader/open_writer。全 FileStore がそれを満たす。
    for store in _file_store_instances(tmp_path):
        assert_file_store(store)


def test_file_store_requires_io_on_top_of_kvs() -> None:
    # 包含関係の確認: FileStore のメンバ ⊇ KVS のメンバ ＋ open_reader/open_writer。
    kvs = required_members(KeyValueStore)
    from manystore.file import FileStore

    fs = required_members(FileStore)
    assert kvs <= fs
    assert fs - kvs == {"open_reader", "open_writer"}


# ── 挙動契約（インプロセス backend で実際に叩く） ──


def test_dict_kvs_satisfies_behavior_contract() -> None:
    asyncio.run(check_key_value_store_contract(DictKeyValueStore()))


def test_dict_file_store_satisfies_behavior_contract() -> None:
    asyncio.run(check_file_store_contract(DictFileStore()))


def test_local_kvs_satisfies_behavior_contract(tmp_path) -> None:
    asyncio.run(check_key_value_store_contract(LocalKeyValueStore(tmp_path)))


def test_local_file_store_satisfies_behavior_contract(tmp_path) -> None:
    asyncio.run(check_file_store_contract(LocalFileStore(tmp_path)))


def test_http_read_only_satisfies_contract() -> None:
    # read-only backend は writable=False＝write 系が io.UnsupportedOperation を投げることを確認
    # （ネットワーク不要＝put/delete/cp/mv/open_writer は接続前に拒否する）。
    asyncio.run(
        check_key_value_store_contract(HttpKeyValueStore(base_url="http://x"), writable=False)
    )
    asyncio.run(check_file_store_contract(HttpFileStore(base_url="http://x"), writable=False))


def test_conformance_detects_missing_method() -> None:
    # メソッドが欠けた偽実装は不足が検出される（ツール自体の健全性）。
    class _Broken:
        async def put(self, key, value): ...

    missing = missing_members(_Broken(), KeyValueStore)
    assert "get_or_raise" in missing
    assert "iter" in missing
    with pytest.raises(AssertionError):
        assert_key_value_store(_Broken())
