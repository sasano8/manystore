"""conformance — ストア実装が抽象 Protocol に準拠するかを検査する再利用ツール。

サードパーティが新しい backend を実装したとき、`pytest` などから簡単に「前提とする Protocol に
準拠しているか」を横断的に確認できるようにするツール。2 段階で確認できる:

1. **メソッド存在チェック**（`assert_key_value_store` / `assert_file_store`）— Protocol メンバが
   callable な属性として在るか。`typing.get_protocol_members`（継承を含む）が対象。
2. **挙動契約テスト**（`FileStoreTester`）— **辞書ストアを正（オラクル）**とし、同じ操作列を
   reference（辞書）と target の両方に適用して観測一致を観点ごとに検証する。結果は JSON 保存でき、
   将来リプレイ（保存結果を別実装へ再適用）に使える。段階実行 run_light < middle < heavy < full。

**シグネチャ検査・spec 自動検出（file/kv 寄り）は未実装**（別タスク M022 P3）。

使い方（サードパーティ backend のテスト例）::

    import asyncio
    from manystore import DictFileStore
    from manystore.conformance import assert_file_store, FileStoreTester

    def test_my_file_store():
        target = MyFileStore()
        assert_file_store(target)                              # メソッドが揃っているか
        tester = FileStoreTester(DictFileStore(), target)     # 正=辞書, 対象=target
        result = asyncio.run(tester.run_light())              # open_reader/open_writer/exists
        assert result["summary"]["failed"] == 0
        tester.save_json("my_file_store.conformance.json")    # 結果を全保存
"""

import base64
import contextlib
import json
import typing
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

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


# ── 挙動契約テストツール（辞書ストアをオラクルに差分比較） ──


def _encode(value: object) -> object:
    """観測値を JSON 可能な形に符号化（bytes は base64・他は素通し）。"""
    if isinstance(value, (bytes, bytearray)):
        return {"bytes_b64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


async def _apply(store: object, op: str, args: dict) -> dict:
    """1 操作を `store` に適用し、観測結果を JSON 可能な dict で返す（リプレイの基本単位）。

    成功は `{"return": <符号化値>}`、例外送出は `{"raised": "<例外クラス名>"}`。op/args が同じなら
    別実装でも同じ観測になるべき＝オラクル（辞書）と対象を同じ op で叩いて比較できる。
    """
    if op not in _OPS:
        raise ValueError(f"unknown op: {op}")
    try:
        return {"return": _encode(await _OPS[op](store, args))}
    except Exception as e:  # noqa: BLE001  観測として例外型を記録する
        return {"raised": type(e).__name__}


async def _op_exists(store: object, args: dict) -> object:
    return await store.exists(args["key"])


async def _op_open_writer_write(store: object, args: dict) -> object:
    data = base64.b64decode(args["data_b64"])
    async with await store.open_writer(args["key"]) as w:
        await w.write(data)
    return None  # 観測は「成功したか」だけ（効果は後続 read/exists で観る）


async def _op_open_reader_read(store: object, args: dict) -> object:
    async with await store.open_reader(args["key"]) as r:
        return await r.read(args.get("n", -1))


async def _op_list_all(store: object, args: dict) -> object:
    return await store.list_all(args.get("limit", 1000))  # 全ファイル平坦（filename 列で観測）


_OPS = {
    "exists": _op_exists,
    "open_writer_write": _op_open_writer_write,
    "open_reader_read": _op_open_reader_read,
    "list_all": _op_list_all,
}


@dataclass
class StepResult:
    """1 観点（操作）の結果。`op`/`args`/`expected` はリプレイにそのまま使える。"""

    aspect: str  # 観点ラベル（例 "exists:missing"）
    op: str  # リプレイ用の操作種別（_OPS のキー）
    args: dict  # リプレイ用の引数（JSON 可能）
    expected: dict  # オラクル（辞書ストア）の観測
    actual: dict  # 対象ストアの観測
    passed: bool  # expected == actual


class FileStoreTester:
    """辞書ストアを**正（オラクル）**とし、対象 [FileStore] の挙動を差分比較するテストツール。

    同じ操作列を reference（辞書）と target に適用し、観測結果が一致するかを観点ごとに記録する。
    結果は `result()`（dict）/`save_json(path)` で取り出せ、`op`/`args`/`expected` を含むので将来
    リプレイ（保存結果を別実装へ再適用）に使える。段階実行: `run_light` < `run_middle` < `run_heavy`
    < `run_full`。**まず `run_light`**＝open_reader / open_writer / exists / list_all を検証する。

    `delete_all` はクリーンな初期状態を作る基盤操作（ジェネシス＝自己循環で検証困難なので
    run_light の検証対象外。使うだけ）。spec（file/kv 寄り 等）の自動検出は別タスク（placeholder）。
    """

    def __init__(self, reference: object, target: object) -> None:
        self._reference = reference  # 辞書ファイルストア（正）
        self._target = target  # テスト対象ファイルストア
        self._ns = f"_conformance/{uuid.uuid4().hex}"  # 衝突回避の名前空間
        self.steps: list[StepResult] = []
        self.spec: dict = {"leaning": None}  # TODO(M022): file寄り/kv寄りの自動検出は別タスク

    async def delete_all(self, store: object) -> None:
        """`store` の全キーを消してクリーンにする（ジェネシス・run_light では検証対象外）。"""
        keys = [info["filename"] async for info in store.iter()]
        for key in keys:
            with contextlib.suppress(Exception):
                await store.delete(key)

    async def _check(self, aspect: str, op: str, args: dict) -> None:
        """同一操作を reference / target に適用し、観測一致を 1 観点として記録する。"""
        expected = await _apply(self._reference, op, args)
        actual = await _apply(self._target, op, args)
        self.steps.append(StepResult(aspect, op, dict(args), expected, actual, expected == actual))

    async def run_light(self) -> dict:
        """light ティア: open_reader / open_writer / exists / list_all（＋欠損）を検証する。"""
        await self.delete_all(self._reference)
        await self.delete_all(self._target)

        key = f"{self._ns}/a"
        payload = base64.b64encode(b"hello\x00\xffworld").decode("ascii")
        v2 = base64.b64encode(b"v2").decode("ascii")

        await self._check("exists:missing", "exists", {"key": key})
        await self._check("open_reader:missing", "open_reader_read", {"key": key, "n": -1})
        await self._check("list_all:empty", "list_all", {})  # クリーン後は空
        await self._check(
            "open_writer:write", "open_writer_write", {"key": key, "data_b64": payload}
        )
        await self._check("exists:after_write", "exists", {"key": key})
        await self._check("list_all:after_write", "list_all", {})  # 書いたキーが全件に現れる
        await self._check("open_reader:full", "open_reader_read", {"key": key, "n": -1})
        await self._check("open_reader:partial", "open_reader_read", {"key": key, "n": 5})
        await self._check(
            "open_writer:overwrite", "open_writer_write", {"key": key, "data_b64": v2}
        )
        await self._check("open_reader:after_overwrite", "open_reader_read", {"key": key, "n": -1})
        return self.result()

    async def run_middle(self) -> dict:
        raise NotImplementedError("run_middle は未実装（M022 P3）")

    async def run_heavy(self) -> dict:
        raise NotImplementedError("run_heavy は未実装（M022 P3）")

    async def run_full(self) -> dict:
        raise NotImplementedError("run_full は未実装（M022 P3）")

    def result(self) -> dict:
        """これまでの観点結果をまとめた JSON 可能な dict（対象のテスト結果）。"""
        passed = sum(1 for s in self.steps if s.passed)
        return {
            "target": type(self._target).__name__,
            "reference": type(self._reference).__name__,
            "spec": self.spec,
            "summary": {
                "total": len(self.steps),
                "passed": passed,
                "failed": len(self.steps) - passed,
            },
            "steps": [asdict(s) for s in self.steps],
        }

    def save_json(self, path: str | Path) -> None:
        """対象のテスト結果を JSON ファイルへ全て保存する（将来リプレイの素材）。"""
        Path(path).write_text(
            json.dumps(self.result(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
