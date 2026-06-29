"""conformance 結果を docs の spec へ出力する CLI 入口。

`python -m manystore.tools.conformancer [--out-dir docs]` で 3 つの spec を生成する:

1. `docs/kv_spec.md` / `docs/file_storage_spec.md`＝各 backend が Protocol の**メソッドを満たすか**
   （Implemented / Not）を **メソッド × 実装** の表に（接続不要・決定的）。
2. `docs/conformance_spec.md`＝ストア実装が満たすべき**挙動契約のカタログ**（絶対契約＋差分観点）。
   絶対契約は [ABSOLUTE_CONTRACTS] の宣言から、差分観点は run_* の**実行から導出**する
   （宣言を二重持ちしない＝仕様書が実態と乖離しない）。

これにより「契約を書く＝テストが生まれ・カバレッジに出て・仕様書化され・新 backend の TODO になる」
（北極星＝conformance を仕様の単一源泉に）。Makefile から `make conformance-docs` でキックする。
"""

import argparse
import asyncio
import tempfile
from pathlib import Path

from ...protocols import AsyncFileStore, AsyncKeyValueStore
from . import (
    ABSOLUTE_CONTRACTS,
    differential_contract_aspects,
    missing_members,
    required_members,
    scaffold_backend,
)


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


def _render_behavioral(absolute: list, differential: list) -> str:
    """挙動契約カタログ（絶対契約＋差分観点）を 1 ファイルの Markdown にする。

    `absolute`＝[ContractSpec] の list（宣言）。`differential`＝`(level, aspect)` の list（run_* の
    実行から導出）。両者を「ストア実装が満たすべき契約一覧」として出力する。
    """
    out = [
        "# 挙動契約 — behavioral conformance spec",
        "",
        "> 自動生成: `make conformance-docs`。手で編集しない。manystore のストア実装が満たすべき",
        "> **挙動契約**の一覧。各契約は conformancer がテストとして実行し（① テスト可能）、",
        "> pytest-cov に現れ（② 網羅可視）、この表に出力され（③ 仕様書）、",
        "> 新 backend 実装の TODO になる（④ scaffold）。",
        "",
        "## 絶対契約（オラクル非依存・全実装が満たす製品必須挙動）",
        "",
        "`manystore.tools.conformancer` の各 assert 関数で検査する。新 backend は接続済みストアを",
        "渡してこれらを呼べば、実装漏れが loud に落ちる。",
        "",
        "| 契約ID | 内容 | 実装する検査 |",
        "|---|---|---|",
    ]
    for c in absolute:
        out.append(f"| `{c.id}` | {c.summary} | `{c.check}` |")
    out += [
        "",
        "## 差分契約（辞書ストアをオラクルに観測一致を検査）",
        "",
        "`FileStoreTester` が辞書ストア（正）と対象に同じ操作を適用し、返り値と適用後状態の一致を",
        "観点ごとに検証する。下記の観点一覧は **run_* の実行から導出**（実態が正）。",
        "",
    ]
    for level in ("light", "middle", "heavy"):
        aspects = [a for lv, a in differential if lv == level]
        out.append(f"### run_{level}")
        out.append("")
        out += [f"- `{a}`" for a in aspects]
        out.append("")
    out += [
        "## 新しい backend の作り方（scaffold の出発点）",
        "",
        "0. 雛形生成: `python -m manystore.tools.conformancer --scaffold MyStore --kind kv|file`",
        "   ＝未実装メソッド（`raise NotImplementedError`）＋満たすべき契約 TODO＋配線手順が出る。",
        "1. `KeyValueStore` / `FileStore` の Protocol メソッドを実装（`kv_spec.md` /",
        "   `file_storage_spec.md` の ✅ を埋める）。`assert_key_value_store` 等で存在チェック。",
        "2. 上記**絶対契約**の assert を接続済みストアに対して呼び、全て緑にする。",
        "3. `FileStoreTester(DictFileStore(), <your_store>)` の `run_light`/`run_middle`/",
        "   `run_heavy` を回し差分観点をオラクルに一致させる（run_* は非破壊）。",
        "   `run_full` は差分（light+middle+heavy）＋絶対契約を 1 レポートに集約する一括実行。",
        "",
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="manystore.tools.conformancer")
    parser.add_argument(
        "--out-dir", default="docs", help="spec 表の出力先ディレクトリ（既定: docs）"
    )
    parser.add_argument(
        "--scaffold",
        metavar="CLASS",
        help="新 backend 実装の雛形を生成して出力（spec は生成しない）。例: --scaffold MyStore",
    )
    parser.add_argument(
        "--kind", choices=["kv", "file"], default="file", help="--scaffold の種別（既定: file）"
    )
    parser.add_argument("--out", help="--scaffold の出力ファイル（既定: 標準出力）")
    ns = parser.parse_args()

    # 雛形生成モード（北極星④＝契約一覧が実装の TODO になる）。spec 生成とは排他。
    if ns.scaffold:
        code = scaffold_backend(ns.scaffold, kind=ns.kind)
        if ns.out:
            Path(ns.out).write_text(code, encoding="utf-8")
            print(f"wrote {ns.out}")
        else:
            print(code, end="")
        return

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

    # 挙動契約カタログ（絶対契約は宣言から・差分観点は run_* の実行から導出）。
    differential = asyncio.run(differential_contract_aspects())
    behavioral = out_dir / "conformance_spec.md"
    behavioral.write_text(_render_behavioral(ABSOLUTE_CONTRACTS, differential), encoding="utf-8")
    print(f"wrote {behavioral}")


if __name__ == "__main__":
    main()
