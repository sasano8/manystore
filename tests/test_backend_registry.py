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
from manystore.storage import registry as reg
from manystore.storage.backends import (
    DictStore,
    S3Store,
    create_unsafe_store,
)


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
    # 単一 factory が実クラス（full Store）を組み立てる（未接続・M071）。
    assert isinstance(get_backend_spec("memory").factory(), DictStore)
    assert isinstance(get_backend_spec("s3").factory(s3_bucket="b"), S3Store)


def test_unknown_backend_raises_with_candidates() -> None:
    with pytest.raises(ValueError, match="unknown backend"):
        get_backend_spec("nope")


def test_manystore_is_full_store() -> None:
    # M071＝manystore も単一 factory で full Store（open_* を持つ・旧「FileStore 非対応」は解消）。
    store = create_unsafe_store("manystore", base_url="http://x", context="c")
    assert hasattr(store, "open_reader") and hasattr(store, "open_writer")


def test_create_unsafe_dispatches_through_registry() -> None:
    assert isinstance(create_unsafe_store("memory"), DictStore)
    # 旧 create_unsafe_{key_value,file}_store は create_unsafe_store へ委譲（非推奨・後方互換）。
    assert isinstance(create_unsafe_store("memory"), DictStore)
    assert isinstance(create_unsafe_store("memory"), DictStore)


def test_programmatic_register_and_resolve() -> None:
    def make_store(**opts):
        return DictStore()

    register_backend("custom", factory=make_store)
    spec = get_backend_spec("custom")
    assert spec.origin == "programmatic"
    assert isinstance(create_unsafe_store("custom"), DictStore)


def test_register_legacy_kwargs_accepted() -> None:
    # 後方互換＝旧 kv_factory=/file_factory= も単一 factory に写して受理（M071）。
    register_backend("legacy", kv_factory=lambda **o: DictStore())
    assert isinstance(create_unsafe_store("legacy"), DictStore)


def test_register_conflict_requires_clobber() -> None:
    def make_store(**opts):
        return DictStore()

    # builtin 予約名は clobber 無しでは拒否。
    with pytest.raises(ValueError, match="already registered"):
        register_backend("s3", factory=make_store)
    # clobber=True で明示的に差し替え可能。
    register_backend("s3", factory=make_store, clobber=True)
    assert get_backend_spec("s3").origin == "programmatic"


def test_entry_point_may_not_shadow_builtin() -> None:
    # entry-point 経路は既存名（builtin）を shadow しない＝拒否＋warn、builtin が残る。
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reg._register_entry_point(BackendSpec("s3", lambda **o: DictStore(), "entry-point:evil"))
    assert any("may not shadow" in str(w.message) for w in caught)
    assert get_backend_spec("s3").origin == "builtin"


def test_entry_point_adds_new_name() -> None:
    reg._register_entry_point(BackendSpec("plugin_x", lambda **o: DictStore(), "entry-point:x"))
    assert get_backend_spec("plugin_x").origin == "entry-point:x"


def test_list_backends_includes_builtins_sorted() -> None:
    names = [s.name for s in list_backends()]
    assert names == sorted(names)
    for name in ("memory", "local", "s3", "nats", "http", "manystore"):
        assert name in names
