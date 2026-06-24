"""conformance — ストア実装が抽象 Protocol に準拠するかを検査する再利用ツール。

サードパーティが新しい backend を実装したとき、`pytest` などから簡単に「前提とする Protocol に
準拠しているか」を横断的に確認できるようにするツール。2 段階で確認できる:

1. **メソッド存在チェック**（`assert_key_value_store` / `assert_file_store`）— Protocol メンバが
   callable な属性として在るか。`typing.get_protocol_members`（継承を含む）が対象。
2. **挙動契約テスト**（`FileStoreTester`）— **辞書ストアを正（オラクル）**とし、同じ操作を
   reference（辞書）と target に適用して観測一致を観点ごとに検証する。各観点は**返り値**だけでなく
   **op 適用後の状態**（iter_all のファイル名一覧・昇順）も取り、両方の一致を見る。run 系に
   **レポート（list）を渡す**と操作順に結果を追記する（ツールはレポートを保持しない）。
   `save_report` で JSON 保存でき将来リプレイに使える。段階実行 run_light<middle<heavy<full。

**シグネチャ検査・spec 自動検出（file/kv 寄り）は未実装**（別タスク M022b）。

使い方（サードパーティ backend のテスト例）::

    import asyncio
    from manystore import DictFileStore
    from manystore.conformance import assert_file_store, FileStoreTester, save_report

    def test_my_file_store():
        target = MyFileStore()
        assert_file_store(target)                              # メソッドが揃っているか
        tester = FileStoreTester(DictFileStore(), target)     # 正=辞書, 対象=target
        report = []                                            # 呼び出し側がレポートを所有
        asyncio.run(tester.run_light(report))                 # 操作順に結果を追記
        assert all(s["passed"] for s in report)
        save_report(report, "my_file_store.conformance.json") # 全保存
"""

import base64
import contextlib
import json
import typing
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .stores.base import FileStore, KeyValueStore


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
    return await store.list_all(args.get("limit", 1000))  # 全キー平坦（filename 列で観測）


async def _op_iter_all(store: object, args: dict) -> object:
    return [info async for info in store.iter_all()]  # 全キー平坦を materialize して観測


async def _state(store: object) -> list:
    """op 適用後の**状態** = `iter_all` で得たファイル名一覧の昇順（JSON 可能）。

    各 op は「返り値」を観測するが、それとは別に「適用後にストアがどんな状態になったか」も
    重要（例: write が値を返さなくても、その後 iter_all にキーが現れるべき）。返り値の一致だけでは
    副作用を見落とすので、**op ごとに状態スナップショットを取り**返り値とあわせて検証する。
    """
    names = [info["filename"] async for info in store.iter_all()]
    return sorted(names)


_OPS = {
    "exists": _op_exists,
    "open_writer_write": _op_open_writer_write,
    "open_reader_read": _op_open_reader_read,
    "list_all": _op_list_all,
    "iter_all": _op_iter_all,
}


@dataclass
class StepResult:
    """1 観点（操作）の結果。`op`/`args`/`expected` はリプレイにそのまま使える。

    返り値（`expected`/`actual`）に加え、**op 適用後の状態**（`expected_state`/`actual_state`＝
    iter_all のファイル名・昇順）も記録する。`passed` は返り値と状態の両方が一致して初めて真。
    """

    aspect: str  # 観点ラベル（例 "exists:missing"）
    op: str  # リプレイ用の操作種別（_OPS のキー）
    args: dict  # リプレイ用の引数（JSON 可能）
    expected: dict  # オラクル（辞書ストア）の返り値観測
    actual: dict  # 対象ストアの返り値観測
    expected_state: list  # op 適用後のオラクルの状態（iter_all のファイル名・昇順）
    actual_state: list  # op 適用後の対象の状態（同上）
    passed: bool  # expected == actual かつ expected_state == actual_state


def save_report(report: list, path: str | Path) -> None:
    """run 系が追記したレポート（ステップ列）を JSON ファイルへ保存する（将来リプレイの素材）。"""
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


class FileStoreTester:
    """辞書ストアを**正（オラクル）**とし、対象 [FileStore] の挙動を差分比較するテストツール。

    run 系（run_light 等）に**レポート（list）を渡す**と、同じ操作を reference（辞書）と target に
    適用し操作順に観測結果（[StepResult] を dict 化）を**追記**する。
    **ツール自身はレポートを保持しない**（呼び出し側が所有し、`save_report(report, path)` で JSON
    保存できる。各エントリは返り値（`expected`/`actual`）と **op 適用後の状態**
    （`expected_state`/`actual_state`＝iter_all ファイル名・昇順）を含み将来リプレイに使える）。
    段階実行 run_light < middle < heavy < full。**まず run_light**＝
    open_reader/open_writer/exists/list_all/iter_all を検証する。

    `delete_all` はクリーンな初期状態を作る基盤操作（ジェネシス＝検証困難なので light 対象外）。
    spec（file/kv 寄り 等）の自動検出は別タスク（M022b）。
    """

    def __init__(self, reference: object, target: object) -> None:
        self._reference = reference  # 辞書ファイルストア（正）
        self._target = target  # テスト対象ファイルストア

    async def delete_all(self, store: object) -> None:
        """`store` の全キーを消してクリーンにする（ジェネシス・run_light では検証対象外）。"""
        keys = [info["filename"] async for info in store.iter_all()]
        for key in keys:
            with contextlib.suppress(Exception):
                await store.delete(key)

    async def _check(self, report: list, aspect: str, op: str, args: dict) -> None:
        """同一操作を reference / target に適用し、1 観点として report に追記する。

        **返り値の観測**（expected/actual）に加え、**op 適用後の状態**（`_state`＝iter_all の
        ファイル名昇順）も両ストアで取り、返り値と状態の両方が一致したときだけ `passed` を真にする。
        """
        expected = await _apply(self._reference, op, args)
        actual = await _apply(self._target, op, args)
        expected_state = await _state(self._reference)
        actual_state = await _state(self._target)
        passed = expected == actual and expected_state == actual_state
        step = StepResult(
            aspect, op, dict(args), expected, actual, expected_state, actual_state, passed
        )
        report.append(asdict(step))

    async def run_light(self, report: list) -> None:
        """light: open_reader/open_writer/exists/list_all/iter_all（欠損含む）を report に追記。"""
        await self.delete_all(self._reference)
        await self.delete_all(self._target)

        ns = f"_conformance/{uuid.uuid4().hex}"  # 衝突回避の名前空間
        key = f"{ns}/a"
        payload = base64.b64encode(b"hello\x00\xffworld").decode("ascii")
        v2 = base64.b64encode(b"v2").decode("ascii")

        await self._check(report, "exists:missing", "exists", {"key": key})
        await self._check(report, "open_reader:missing", "open_reader_read", {"key": key, "n": -1})
        await self._check(report, "list_all:empty", "list_all", {})  # クリーン後は空
        await self._check(report, "iter_all:empty", "iter_all", {})
        await self._check(
            report, "open_writer:write", "open_writer_write", {"key": key, "data_b64": payload}
        )
        await self._check(report, "exists:after_write", "exists", {"key": key})
        await self._check(report, "list_all:after_write", "list_all", {})  # 全件にキーが現れる
        await self._check(report, "iter_all:after_write", "iter_all", {})
        await self._check(report, "open_reader:full", "open_reader_read", {"key": key, "n": -1})
        await self._check(report, "open_reader:partial", "open_reader_read", {"key": key, "n": 5})
        await self._check(
            report, "open_writer:overwrite", "open_writer_write", {"key": key, "data_b64": v2}
        )
        await self._check(
            report, "open_reader:after_overwrite", "open_reader_read", {"key": key, "n": -1}
        )

    async def run_middle(self, report: list) -> None:
        raise NotImplementedError("run_middle は未実装（M022b）")

    async def run_heavy(self, report: list) -> None:
        raise NotImplementedError("run_heavy は未実装（M022b）")

    async def run_full(self, report: list) -> None:
        raise NotImplementedError("run_full は未実装（M022b）")
