"""config — context マウント定義と views.featured（ビュー重点設定）を読み込む。

TOML（または同等の dict）から:
- `[contexts.<name>]` … 公開する context 名 → backend と backend 固有パラメータ。
- `[[views.featured]]` … UI が「標準で重点的に扱う」対象（pin / quick_write 等）の宣言。
- `default_context` … UI が既定で開く context（任意）。

config は「重点パス」を宣言するだけで、interrupt のような特定用途を UI 本体は知らない＝汎用のまま。
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ContextConfig:
    """1 つの context マウント。

    `opts` は backend 固有引数（`create_unsafe_key_value_store` へ渡す）。
    """

    name: str
    backend: str
    opts: dict[str, object] = field(default_factory=dict)
    writable: bool = True


@dataclass(frozen=True)
class FeaturedView:
    """ビューの重点設定。UI のサイドバー上部に固定したい/即書き込みしたいパスの宣言。"""

    context: str
    path: str = ""  # context 内の prefix（空なら context 全体）
    label: str = ""
    pin: bool = False
    quick_write: bool = False


@dataclass(frozen=True)
class AppConfig:
    """アプリ全体の設定（マウント + ビュー重点設定）。"""

    contexts: dict[str, ContextConfig] = field(default_factory=dict)
    featured: list[FeaturedView] = field(default_factory=list)
    default_context: str = ""


# backend 名 → `create_unsafe_key_value_store` のキーワード接頭辞のうち、よく使うものを
# トップレベルキーから補完する（例: local context の `root` を `local_dir` に写す）。
def _normalize_opts(backend: str, raw: dict[str, object]) -> dict[str, object]:
    opts = dict(raw)
    if backend == "local":
        # `root` を local backend の `local_dir` に写す（TOML 側は直感的な root を使える）。
        root = opts.pop("root", None)
        if root is not None:
            opts["local_dir"] = Path(str(root))
    return opts


def parse_config(data: dict[str, object]) -> AppConfig:
    """既に読み込んだ dict（TOML 相当）から [AppConfig] を組み立てる。"""
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
            opts=_normalize_opts(backend, opts),
            writable=writable,
        )

    featured: list[FeaturedView] = []
    views = data.get("views", {})
    if isinstance(views, dict):
        raw_featured = views.get("featured", [])
        if isinstance(raw_featured, list):
            for item in raw_featured:
                if not isinstance(item, dict):
                    continue
                ctx = str(item.get("context", ""))
                if not ctx:
                    raise ValueError("featured view requires `context`")
                featured.append(
                    FeaturedView(
                        context=ctx,
                        path=str(item.get("path", "")),
                        label=str(item.get("label", "")),
                        pin=bool(item.get("pin", False)),
                        quick_write=bool(item.get("quick_write", False)),
                    )
                )

    default_context = str(data.get("default_context", ""))
    return AppConfig(contexts=contexts, featured=featured, default_context=default_context)


def load_config(path: str | Path) -> AppConfig:
    """TOML ファイルを読み込んで [AppConfig] を返す。"""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return parse_config(data)
