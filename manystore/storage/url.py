"""store URL パーサ — fsspec 風の `scheme://…` を `(backend, opts)` に分解する（M069）。

`scheme`＝backend 名（[registry]）／`netloc`＝bucket（ストア粒度）／`query`＝backend 固有の接続
オプション、に写る。文法は `docs/url_scheme.md`。ここは純関数＝ストア構築・接続はしない
（`open_store` が本結果を顔 `open_async_key_value_store` に渡す）。

opts は**既存の flat kwargs 形**（`s3_bucket=` / `s3_endpoint=` …）へ写す＝既存 factory を無改修で
使う（後方互換。backend ネイティブ opts への整理は別途）。
"""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def _query_dict(raw: str) -> dict[str, str]:
    # 同名キーは最後の値を採用（keep_blank_values で `?flag=` も拾う）。
    return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def parse_store_url(url: str) -> tuple[str, dict[str, object]]:
    """`scheme://…` を `(backend, opts)` に分解する。scheme が無ければ [ValueError]。

    例:
        parse_store_url("s3://bkt?endpoint=http://h:9000")
            -> ("s3", {"s3_bucket": "bkt", "s3_endpoint": "http://h:9000"})
        parse_store_url("local://.") -> ("local", {"local_dir": Path(".")})
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    if not scheme:
        raise ValueError(f"store URL requires a scheme (例 's3://bkt'): {url!r}")
    netloc = parts.netloc  # = bucket / context（http は base_url の一部）
    q = _query_dict(parts.query)

    if scheme == "memory":
        return "memory", {}

    if scheme == "local":
        # netloc+path を root に。`local://.`=cwd / `local:///abs`=絶対 / `local://./rel`=cwd 相対。
        root = (netloc + parts.path) or "."
        return "local", {"local_dir": Path(root)}

    if scheme == "s3":
        opts: dict[str, object] = {"s3_bucket": netloc}
        # 非秘密（endpoint/region/addressing）＋任意で資格情報（未指定は boto 既定チェーンへ委任）。
        for qk, ok in (
            ("endpoint", "s3_endpoint"),
            ("region", "s3_region"),
            ("access_key", "s3_access_key"),
            ("secret_key", "s3_secret_key"),
            ("addressing_style", "s3_addressing_style"),
        ):
            if qk in q:
                opts[ok] = q[qk]
        return "s3", opts

    if scheme == "nats":
        opts = {"nats_bucket": netloc}
        if "server" in q:  # NATS サーバ URL（bucket とは別レイヤ＝query に分離）
            opts["nats_url"] = q["server"]
        return "nats", opts

    if scheme in ("http", "https"):
        # 例外＝URL 全体が base_url（bucket 概念なし・read-only backend）。scheme を保持して再構成。
        return "http", {"http_base_url": f"{scheme}://{netloc}{parts.path}"}

    if scheme == "manystore":
        opts = {"context": netloc}  # netloc = context（bucket）
        if "server" in q:  # manystore サーバの NS ルート（例 http://host/kv/raw）
            opts["base_url"] = q["server"]
        return "manystore", opts

    # 未知 scheme = plugin backend 名として registry に委ねる。netloc=bucket・query を素通し。
    opts = dict(q)
    if netloc:
        opts["bucket"] = netloc
    return scheme, opts
