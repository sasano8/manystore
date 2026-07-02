"""backend registry — 「名前 → ストア生成」の唯一の解決点（fsspec 風）。

3 経路で backend を登録し、**flat な名前空間**で解決する:

- **builtin**（同梱）… import 時に seed。名前は**予約**（plugin は同名で shadow 不可）。
- **entry-point**（plugin）… group `manystore.stores` を**遅延**発見。既存名への衝突は拒否。
- **programmatic**（[register_backend]）… 明示コール。既存名は `clobber=True` のときだけ上書き。

各エントリは出自（`origin`）を持つ。lookup は flat（`s3` はどの経路でも `s3` で引ける）だが、
builtin 名を plugin が黙って乗っ取れないよう tier を分けてある（supply-chain 安全）。詳細は
`docs/backend_registry.md`。
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass

from ...protocols import AsyncStreamingStore

#: plugin 発見に使う entry-point group（EP 名＝backend/scheme 名）。
ENTRY_POINT_GROUP = "manystore.stores"

StoreFactory = Callable[..., AsyncStreamingStore]  # full Store（put/get＋open_*）を作る
KVFactory = StoreFactory  # 後方互換 alias（旧 kv_factory 型名）
FileFactory = StoreFactory


@dataclass(frozen=True)
class BackendSpec:
    """1 つの backend の生成方法（未接続の full Store を作る単一 factory）と出自（M071・M068）。

    `factory` は backend 固有の `**opts` を受け、**未接続の full Store**（put/get＋open_*）を返す。
    M071 で backend は 1 クラス＝factory も 1 本に統合（旧 kv_factory/file_factory は廃止）。
    """

    name: str
    factory: StoreFactory
    origin: str = "programmatic"  # "builtin" | "entry-point:<dist>" | "programmatic"


def _resolve_factory(
    factory: StoreFactory | None, kv_factory: StoreFactory | None, file_factory: StoreFactory | None
) -> StoreFactory:
    """単一 `factory` を解決（旧 `kv_factory`/`file_factory` kwargs も後方互換で受理・M071）。"""
    f = factory or file_factory or kv_factory
    if f is None:
        raise ValueError("register: factory (or legacy kv_factory/file_factory) is required")
    return f


_REGISTRY: dict[str, BackendSpec] = {}
_ENTRY_POINTS_LOADED = False  # entry-point 走査を一度だけ行うためのフラグ


def register_builtin_backend(
    name: str,
    *,
    factory: StoreFactory | None = None,
    kv_factory: StoreFactory | None = None,
    file_factory: StoreFactory | None = None,
) -> None:
    """同梱 backend を予約名として seed する（`backends` パッケージの import 時に呼ぶ内部 API）。"""
    _REGISTRY[name] = BackendSpec(
        name, _resolve_factory(factory, kv_factory, file_factory), "builtin"
    )


def register_backend(
    name: str,
    *,
    factory: StoreFactory | None = None,
    kv_factory: StoreFactory | None = None,
    file_factory: StoreFactory | None = None,
    clobber: bool = False,
) -> None:
    """programmatic に backend を登録する（自プロセス）。単一 `factory`（旧 kv/file kwargs も可）。

    既存名は `clobber=True` のときだけ上書き可（builtin 予約名の差し替えもこれ経由）。
    それ以外は [ValueError]。
    """
    existing = _REGISTRY.get(name)
    if existing is not None and not clobber:
        raise ValueError(
            f"backend {name!r} already registered (origin={existing.origin}); "
            "pass clobber=True to override"
        )
    _REGISTRY[name] = BackendSpec(
        name, _resolve_factory(factory, kv_factory, file_factory), "programmatic"
    )


def _register_entry_point(spec: BackendSpec) -> None:
    """entry-point 由来を登録。既存名（builtin/他 EP）とは衝突させない（拒否＋warn）。"""
    existing = _REGISTRY.get(spec.name)
    if existing is not None:
        warnings.warn(
            f"manystore backend plugin {spec.name!r} ignored: name already registered "
            f"(origin={existing.origin}); plugins may not shadow existing backends",
            stacklevel=2,
        )
        return
    _REGISTRY[spec.name] = spec


def _load_entry_points() -> None:
    """group `manystore.stores` の plugin を一度だけ走査して登録する（遅延・失敗は握って warn）。"""
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True

    from importlib.metadata import entry_points

    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            target = ep.load()
            spec = target() if callable(target) else target
            if not isinstance(spec, BackendSpec):
                raise TypeError(f"expected BackendSpec, got {type(spec).__name__}")
            # EP 名を正本にし、出自を記録する（配布名が取れれば併記）。
            dist = getattr(getattr(ep, "dist", None), "name", None)
            origin = f"entry-point:{dist}" if dist else "entry-point"
            _register_entry_point(BackendSpec(ep.name, spec.factory, origin))
        except Exception as exc:  # plugin の import/生成失敗は他を巻き込まない
            warnings.warn(
                f"failed to load manystore backend plugin {ep.name!r}: {exc}",
                stacklevel=2,
            )


def get_backend_spec(name: str) -> BackendSpec:
    """backend 名を [BackendSpec] へ解決（builtin→遅延 entry-point）。無ければ [ValueError]。"""
    spec = _REGISTRY.get(name)
    if spec is None:
        _load_entry_points()
        spec = _REGISTRY.get(name)
    if spec is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown backend: {name!r} (known: {known})")
    return spec


def list_backends() -> list[BackendSpec]:
    """登録済み backend を（entry-point も発見して）名前順に列挙する（由来つき・診断用）。"""
    _load_entry_points()
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]
