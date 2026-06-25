"""conformance 結果を docs の spec 表へ出力する CLI 入口。

`python -m manystore.tools.conformancer [--out-dir docs]` で、各 backend 実装が Protocol の
メソッドを満たすか（Implemented / Not）を **メソッド × 実装** の表にして
`docs/kv_spec.md`（KeyValueStore）/ `docs/file_storage_spec.md`（FileStore）へ書き出す。

メソッド存在チェック（接続不要・決定的）を正本にする＝実 backend なしで CI からも回せる。
挙動契約テスト（[FileStoreTester]）は実 backend を要するため、ここでは扱わない。

Makefile から `make conformance-docs` でキックする。
"""

import argparse
import tempfile
from pathlib import Path

from ...protocols import AsyncFileStore, AsyncKeyValueStore
from . import missing_members, required_members


def _kv_instances(tmp_path: str) -> list:
    """KeyValueStore 実装のロスター（接続はしない＝生成だけ）。"""
    from manystore import (
        DictKeyValueStore,
        HttpKeyValueStore,
        LocalKeyValueStore,
        NatsObjectKeyValueStore,
        S3KeyValueStore,
    )
    from manystore.client import RemoteKeyValueStore

    return [
        DictKeyValueStore(),
        LocalKeyValueStore(tmp_path),
        S3KeyValueStore(bucket="b"),
        NatsObjectKeyValueStore(url="nats://x", bucket="b"),
        HttpKeyValueStore(base_url="http://x"),
        RemoteKeyValueStore("http://x", "ctx"),
    ]


def _file_instances(tmp_path: str) -> list:
    """FileStore 実装のロスター（接続はしない＝生成だけ）。"""
    from manystore import (
        DictFileStore,
        HttpFileStore,
        LocalFileStore,
        NatsFileStore,
        S3FileStore,
    )

    return [
        DictFileStore(),
        LocalFileStore(tmp_path),
        S3FileStore(bucket="b"),
        NatsFileStore(url="nats://x", bucket="b"),
        HttpFileStore(base_url="http://x"),
    ]


def _spec_table(protocol: type, stores: list) -> str:
    """メソッド × 実装 の Markdown 表を返す（✅ Implemented / ❌ Not）。"""
    methods = sorted(required_members(protocol))
    names = [type(s).__name__ for s in stores]
    missing_by_store = [missing_members(s, protocol) for s in stores]

    lines = ["| メソッド | " + " | ".join(names) + " |"]
    lines.append("|---|" + "|".join(["---"] * len(names)) + "|")
    for method in methods:
        cells = ["❌" if method in miss else "✅" for miss in missing_by_store]
        lines.append(f"| `{method}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render(protocol: type, title: str, stores: list) -> str:
    """spec ドキュメント 1 ファイル分の本文を生成する。"""
    return (
        f"# {title} — conformance spec\n\n"
        "> 自動生成: `make conformance-docs`（`python -m manystore.tools.conformancer`）。\n"
        "> 手で編集しない。各実装が Protocol のメソッドを満たすか（メソッド存在チェック）を示す。\n"
        f"> ✅ = Implemented / ❌ = Not（`{protocol.__name__}`）。\n\n"
        f"{_spec_table(protocol, stores)}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="manystore.tools.conformancer")
    parser.add_argument(
        "--out-dir", default="docs", help="spec 表の出力先ディレクトリ（既定: docs）"
    )
    ns = parser.parse_args()

    out_dir = Path(ns.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_path:
        targets = [
            (out_dir / "kv_spec.md", AsyncKeyValueStore, "KeyValueStore", _kv_instances(tmp_path)),
            (
                out_dir / "file_storage_spec.md",
                AsyncFileStore,
                "FileStore",
                _file_instances(tmp_path),
            ),
        ]
        for path, protocol, title, stores in targets:
            path.write_text(_render(protocol, title, stores), encoding="utf-8")
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
