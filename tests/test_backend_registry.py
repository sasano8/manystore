"""backend レジストリ（M068）のテスト。

builtin 解決・programmatic 登録・clobber 保護・entry-point 非 shadow・由来一覧を検証する。
グローバルな `_REGISTRY` を汚さないよう各テストでスナップショット復元する。
"""

import warnings

import pytest

from manystore import (
    BackendSpec,
    get_backend_spec,
    list_backends,
    register_backend,
)
from manystore.storage.backends import (
    DictKeyValueStore,
    S3KeyValueStore,
    create_unsafe_file_store,
    create_unsafe_key_value_store,
)
from manystore.storage.backends import registry as reg


@pytest.fixture(autouse=True)
def _isolate_registry():
    """各テストで registry と entry-point ロード済みフラグを退避・復元する。"""
    saved = dict(reg._REGISTRY)
    saved_loaded = reg._ENTRY_POINTS_LOADED
    try:
        yield
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)
        reg._ENTRY_POINTS_LOADED = saved_loaded


def test_builtins_resolve_with_origin() -> None:
    for name in ("memory", "local", "s3", "nats", "http", "manystore"):
        assert get_backend_spec(name).origin == "builtin"
    # factory は実クラスを組み立てる（未接続）。
    assert isinstance(get_backend_spec("memory").kv_factory(), DictKeyValueStore)
    assert isinstance(get_backend_spec("s3").kv_factory(s3_bucket="b"), S3KeyValueStore)


def test_unknown_backend_raises_with_candidates() -> None:
    with pytest.raises(ValueError, match="unknown backend"):
        get_backend_spec("nope")


def test_manystore_has_no_file_store() -> None:
    assert get_backend_spec("manystore").file_factory is None
    with pytest.raises(ValueError, match="does not provide a FileStore"):
        create_unsafe_file_store("manystore", base_url="http://x", context="c")


def test_create_unsafe_dispatches_through_registry() -> None:
    assert isinstance(create_unsafe_key_value_store("memory"), DictKeyValueStore)
    assert isinstance(create_unsafe_file_store("memory"), type(create_unsafe_file_store("memory")))


def test_programmatic_register_and_resolve() -> None:
    def make_kv(**opts):
        return DictKeyValueStore()

    register_backend("custom", kv_factory=make_kv)
    spec = get_backend_spec("custom")
    assert spec.origin == "programmatic"
    assert isinstance(create_unsafe_key_value_store("custom"), DictKeyValueStore)


def test_register_conflict_requires_clobber() -> None:
    def make_kv(**opts):
        return DictKeyValueStore()

    # builtin 予約名は clobber 無しでは拒否。
    with pytest.raises(ValueError, match="already registered"):
        register_backend("s3", kv_factory=make_kv)
    # clobber=True で明示的に差し替え可能。
    register_backend("s3", kv_factory=make_kv, clobber=True)
    assert get_backend_spec("s3").origin == "programmatic"


def test_entry_point_may_not_shadow_builtin() -> None:
    # entry-point 経路は既存名（builtin）を shadow しない＝拒否＋warn、builtin が残る。
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reg._register_entry_point(
            BackendSpec("s3", kv_factory=lambda **o: DictKeyValueStore(), origin="entry-point:evil")
        )
    assert any("may not shadow" in str(w.message) for w in caught)
    assert get_backend_spec("s3").origin == "builtin"


def test_entry_point_adds_new_name() -> None:
    reg._register_entry_point(
        BackendSpec("plugin_x", kv_factory=lambda **o: DictKeyValueStore(), origin="entry-point:x")
    )
    assert get_backend_spec("plugin_x").origin == "entry-point:x"


def test_list_backends_includes_builtins_sorted() -> None:
    names = [s.name for s in list_backends()]
    assert names == sorted(names)
    for name in ("memory", "local", "s3", "nats", "http", "manystore"):
        assert name in names
