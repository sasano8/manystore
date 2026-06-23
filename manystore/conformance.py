"""conformance — ストア実装が抽象 Protocol に準拠するかを検査する再利用ツール。

サードパーティが新しい backend を実装したとき、`pytest` などから簡単に「前提とする Protocol の
メソッドが揃っているか」を横断的に確認できるようにする最小ツール。**現状はメソッド存在チェックのみ**
（開発途上のため、挙動の一致＝契約スイートは未実装。必要になってから足す）。

使い方（サードパーティ backend のテスト例）::

    from manystore.conformance import assert_key_value_store, assert_file_store

    def test_my_backend_conforms():
        assert_key_value_store(MyKeyValueStore(...))   # KVS メソッドが揃っているか
        assert_file_store(MyFileStore(...))            # FileStore（= KVS + IO）が揃っているか

存在チェックは `typing.get_protocol_members` が返す Protocol メンバ（継承を含む）を対象にし、
インスタンスに callable な属性として在るかを見る（シグネチャ・挙動は対象外＝別フェーズ）。
"""

import typing

from .async_storage import FileStore, KeyValueStore


def required_members(protocol: type) -> frozenset[str]:
    """`protocol` が要求するメンバ名の集合（継承した Protocol のメンバも含む）。"""
    return typing.get_protocol_members(protocol)


def missing_members(obj: object, protocol: type) -> set[str]:
    """`obj` に欠けている、または callable でない `protocol` メンバ名の集合。"""
    return {name for name in required_members(protocol) if not callable(getattr(obj, name, None))}


def assert_implements(obj: object, protocol: type) -> None:
    """`obj` が `protocol` の全メソッドを（callable な属性として）持つことを表明する。

    欠けていれば `AssertionError`（不足メンバ名を列挙）。挙動・シグネチャは検査しない。
    """
    missing = missing_members(obj, protocol)
    if missing:
        raise AssertionError(
            f"{type(obj).__name__} は {protocol.__name__} の "
            f"{sorted(missing)} を実装していません（メソッド存在チェック）"
        )


def assert_key_value_store(obj: object) -> None:
    """`obj` が [KeyValueStore] の全メソッドを持つことを表明する。"""
    assert_implements(obj, KeyValueStore)


def assert_file_store(obj: object) -> None:
    """`obj` が [FileStore]（= KeyValueStore + open_reader/open_writer）を持つことを表明する。"""
    assert_implements(obj, FileStore)
