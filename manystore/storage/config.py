"""store config — 構成ファイル（`manystore.toml`）からストア構成を復元する（M070）。

`[contexts.<name>]`（backend ＋ backend 固有 opts）を読み、名前 → ストア生成情報に落とす。**local の
相対パスは構成ファイルのディレクトリ基準**で解決する（cwd 非依存）。上方向 discovery（親を辿って
`manystore.toml` を探す）で「その構成ファイルをカレント扱い」にできる。

serving 層（`serving/services/config.py` の `AppConfig`＝views/featured 付き）もこの neutral な
`ContextConfig`/`parse_contexts`/`normalize_opts` を再利用する（構成の二重持ち＝drift を避ける）。
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: 上方向 discovery で探す既定の構成ファイル名。
CONFIG_FILENAME = "manystore.toml"


@dataclass(frozen=True)
class ContextConfig:
    """1 つの context（名前付きストア）。`opts` は backend 固有引数（factory へ渡す）。"""

    name: str
    backend: str
    opts: dict[str, object] = field(default_factory=dict)
    writable: bool = True


@dataclass(frozen=True)
class StoreConfig:
    """構成ファイル 1 つ分（context 群 ＋ 既定 context ＋ 相対パス解決基準）。"""

    contexts: dict[str, ContextConfig] = field(default_factory=dict)
    default_context: str = ""
    base_dir: Path = field(
        default_factory=Path
    )  # local 相対パスの解決基準（構成ファイルのディレクトリ）


def normalize_opts(backend: str, raw: dict[str, object], base_dir: Path) -> dict[str, object]:
    """backend 固有の正規化。local は `root`（相対は `base_dir` 基準で絶対化）を `local_dir` へ。"""
    opts = dict(raw)
    if backend == "local":
        root = opts.pop("root", None)
        if root is not None:
            p = Path(str(root))
            opts["local_dir"] = p if p.is_absolute() else (base_dir / p)
    return opts


def parse_contexts(data: dict[str, object], *, base_dir: Path) -> dict[str, ContextConfig]:
    """`data["contexts"]` を `{name: ContextConfig}` に解く（local 相対は `base_dir` 基準）。"""
    raw_contexts = data.get("contexts", {})
    if not isinstance(raw_contexts, dict):
        raise ValueError("`contexts` must be a table")
    contexts: dict[str, ContextConfig] = {}
    for name, body in raw_contexts.items():
        if not isinstance(body, dict):
            raise ValueError(f"context {name!r} must be a table")
        backend = str(body.get("backend", ""))
        if not backend:
            raise ValueError(f"context {name!r} requires `backend`")
        writable = bool(body.get("writable", True))
        opts = {k: v for k, v in body.items() if k not in ("backend", "writable")}
        contexts[name] = ContextConfig(
            name=name,
            backend=backend,
            opts=normalize_opts(backend, opts, base_dir),
            writable=writable,
        )
    return contexts


def parse_store_config(data: dict[str, object], *, base_dir: Path) -> StoreConfig:
    """既に読み込んだ dict（TOML 相当）から [StoreConfig] を組み立てる。"""
    return StoreConfig(
        contexts=parse_contexts(data, base_dir=base_dir),
        default_context=str(data.get("default_context", "")),
        base_dir=base_dir,
    )


def load_store_config(path: str | Path) -> StoreConfig:
    """TOML 構成ファイルを読み込む。相対パス解決基準はそのファイルのディレクトリ。"""
    path = Path(path)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return parse_store_config(data, base_dir=path.parent)


def find_config_file(
    start: str | Path | None = None, *, filename: str = CONFIG_FILENAME
) -> Path | None:
    """`start`（既定 cwd）から親へ辿って構成ファイルを探す（git/pyproject 風）。無ければ None。"""
    d = Path(start or Path.cwd()).resolve()
    for cand in (d, *d.parents):
        p = cand / filename
        if p.is_file():
            return p
    return None


def discover_store_config(start: str | Path | None = None) -> StoreConfig | None:
    """上方向 discovery で構成ファイルを探して読み込む。見つからなければ None。"""
    p = find_config_file(start)
    return load_store_config(p) if p is not None else None
