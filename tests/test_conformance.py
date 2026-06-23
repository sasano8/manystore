"""横断的な準拠テスト。

(1) 全 backend が `KeyValueStore` / `FileStore` Protocol のメソッドを揃えているか（存在チェック）、
(2) `FileStoreTester` が辞書ストアをオラクルに対象の挙動（run_light）を差分検証できるか、を確認。
サードパーティ backend も `manystore.conformance` を import すれば同じ検査を回せる。
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
    FileStoreTester,
    assert_file_store,
    assert_key_value_store,
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


# ── 挙動契約テストツール（辞書ストアをオラクルに run_light） ──


def test_run_light_local_file_store_matches_oracle(tmp_path) -> None:
    # 辞書ストアを正として LocalFileStore の open_reader/open_writer/exists を差分検証。
    tester = FileStoreTester(DictFileStore(), LocalFileStore(tmp_path))
    result = asyncio.run(tester.run_light())
    assert result["summary"]["failed"] == 0, result["steps"]
    assert result["summary"]["total"] == 8
    assert result["target"] == "LocalFileStore"
    assert result["reference"] == "DictFileStore"


def test_run_light_dict_self_consistent() -> None:
    # 正=対象=辞書ストアなら全観点一致（ツールの健全性）。
    tester = FileStoreTester(DictFileStore(), DictFileStore())
    result = asyncio.run(tester.run_light())
    assert result["summary"]["failed"] == 0


def test_run_light_detects_divergence(tmp_path) -> None:
    # 壊れた実装（書いても保存されない）は観点が fail する＝ツールが差分を検出する。
    class _NoopWriter:
        async def write(self, data):
            return len(data)

        async def close(self): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc): ...

    broken = LocalFileStore(tmp_path)

    async def open_writer(filename):  # 書き込みを握り潰す壊れた open_writer
        return _NoopWriter()

    broken.open_writer = open_writer
    tester = FileStoreTester(DictFileStore(), broken)
    result = asyncio.run(tester.run_light())
    assert result["summary"]["failed"] > 0  # 書けていない→read/exists がオラクルと食い違う


def test_run_light_saves_json(tmp_path) -> None:
    import json

    tester = FileStoreTester(DictFileStore(), LocalFileStore(tmp_path))
    asyncio.run(tester.run_light())
    out = tmp_path / "result.json"
    tester.save_json(out)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["steps"][0]["op"] == "exists"  # op/args/expected が残る（リプレイ素材）
    assert "expected" in saved["steps"][0]
    assert saved["spec"] == {"leaning": None}


def test_conformance_detects_missing_method() -> None:
    # メソッドが欠けた偽実装は不足が検出される（ツール自体の健全性）。
    class _Broken:
        async def put(self, key, value): ...

    missing = missing_members(_Broken(), KeyValueStore)
    assert "get_or_raise" in missing
    assert "iter" in missing
    with pytest.raises(AssertionError):
        assert_key_value_store(_Broken())
