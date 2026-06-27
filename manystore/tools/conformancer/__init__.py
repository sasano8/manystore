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

シグネチャ検査は実装済＝**基底↔Protocol parity**（`assert_base_protocol_parity`）と
**conformancer↔Protocol drift**（`assert_conformancer_protocol_current`・protocols.py が正）。
**spec 自動検出（file/kv 寄り）は未実装**（別タスク M022b）。

使い方（サードパーティ backend のテスト例）::

    import asyncio
    from manystore import DictFileStore
    from manystore.tools.conformancer import assert_file_store, FileStoreTester, save_report

    def test_my_file_store():
        target = MyFileStore()
        assert_file_store(target)                              # メソッドが揃っているか
        tester = FileStoreTester(DictFileStore(), target)     # 正=辞書, 対象=target
        report = []                                            # 呼び出し側がレポートを所有
        asyncio.run(tester.run_light(report))                 # 操作順に結果を追記
        assert all(s["passed"] for s in report)
        save_report(report, "my_file_store.conformance.json") # 全保存
"""

import asyncio
import base64
import contextlib
import inspect
import json
import typing
import uuid
from dataclasses import dataclass, fields
from pathlib import Path

from ...exceptions import ConflictError
from ...protocols import AsyncFileObject, AsyncFileStore, AsyncKeyValueStore, FileInfo


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


def base_protocol_parity_errors(base: type, protocol: type) -> list[str]:
    """基底 `base` が `protocol` を **網羅し、各メソッドのシグネチャが一致するか** を検査する。

    存在チェック（`assert_implements`）は instance がメンバを *持つ* かを見る。これは
    **契約（Protocol）と既定実装（基底クラス）の lockstep** を見る: Protocol が要求する全メンバが
    基底に（abstract か concrete で）宣言され、かつシグネチャ（引数名・既定値・並び・返り値注釈）が
    Protocol と一致するか。崩れると「基底が一部メソッドを宣言せず部分実装が黙って Protocol を破る」
    「基底と Protocol が別々に drift する」のを取り逃す（M043）。違反メッセージ list（空＝OK）。
    """
    errors: list[str] = []
    for name in sorted(required_members(protocol)):
        proto_member = getattr(protocol, name, None)
        base_member = getattr(base, name, None)
        if not callable(base_member):
            errors.append(
                f"{base.__name__} は {protocol.__name__}.{name} を宣言していない"
                f"（基底が Protocol を網羅していない＝部分実装が黙って通りうる）"
            )
            continue
        try:
            proto_sig = inspect.signature(proto_member)
            base_sig = inspect.signature(base_member)
        except Exception:  # noqa: BLE001  シグネチャを取れないメンバは存在チェックに留める
            continue
        if str(base_sig) != str(proto_sig):
            errors.append(
                f"{base.__name__}.{name} のシグネチャが {protocol.__name__} と不一致: "
                f"基底 {base_sig} ≠ Protocol {proto_sig}"
            )
    return errors


def assert_base_protocol_parity(base: type, protocol: type) -> None:
    """`base` が `protocol` を網羅しシグネチャも一致することを表明する（基底↔Protocol lockstep）。

    違反があれば `AssertionError`（網羅漏れ・シグネチャ不一致を列挙）。`KeyValueStoreBase`↔
    `AsyncKeyValueStore` / `FileStoreBase`↔`AsyncFileStore` を対称に点検する用途（M043）。
    """
    errors = base_protocol_parity_errors(base, protocol)
    if errors:
        raise AssertionError(
            f"基底↔Protocol parity 違反（{base.__name__} ↔ {protocol.__name__}）:\n  "
            + "\n  ".join(errors)
        )


def signature_drift(protocol: type, expected: dict[str, str]) -> list[str]:
    """`protocol` の各メンバの現シグネチャが `expected`（写し）と一致するか検査する汎用ヘルパ。

    `expected` は `{メンバ名: str(inspect.signature(...)) の写し}`。`protocol`（正）が `expected`
    と食い違ったら、写しを持つ側が古い契約を前提にしている合図。違反メッセージ list（空＝一致）。
    """
    errors: list[str] = []
    members = required_members(protocol)
    for name, pinned in expected.items():
        if name not in members:
            errors.append(
                f"{protocol.__name__} に前提メンバ {name} が無い（protocols.py で改廃された）"
            )
            continue
        live = str(inspect.signature(getattr(protocol, name)))
        if live != pinned:
            errors.append(f"{protocol.__name__}.{name}: 前提 [{pinned}] ≠ protocols.py [{live}]")
    return errors


# ── conformancer 自身が前提とする Protocol（挙動テスト _OPS / FileObject 操作の写し） ──
#
# FileStoreTester（_OPS・_op_*）は `store.list_all(limit)` / `store.open_reader(key)` → `.read(n)` /
# `.write(data)` のように Protocol の **呼び出し方を直書き**している。protocols.py が進化してここと
# 食い違うと、conformancer は **古いプロトコルを前提に**テストし続ける（黙って誤検証）。下記は
# conformancer が叩く Protocol メンバとその時点のシグネチャの写し（**protocols.py が正**）。drift
# したら _op_* の呼び出しを新契約へ追従させ、この写しも更新する。逆（conformancer 先行・
# protocols.py 未更新）は想定しない＝常に protocols.py を正として直す。
_PINNED_STORE_SIGNATURES = {
    "exists": "(self, key: str) -> bool",
    "delete": "(self, key: str) -> None",
    "open_reader": "(self, filename: str) -> manystore.protocols.AsyncFileObject",
    "open_writer": "(self, filename: str) -> manystore.protocols.AsyncFileObject",
    "iter_all": (
        "(self, limit: int | None = None, prefix: str = '') -> "
        "collections.abc.AsyncIterable[manystore.protocols.FileInfo]"
    ),
    "list_all": (
        "(self, limit: int | None = None, prefix: str = '') -> list[manystore.protocols.FileInfo]"
    ),
}
_PINNED_FILEOBJECT_SIGNATURES = {
    "read": "(self, size: int = -1) -> bytes",
    "write": "(self, data: bytes) -> int",
    "close": "(self) -> None",
}


def conformancer_protocol_drift() -> list[str]:
    """conformancer 前提の Protocol メソッドのシグネチャが protocols.py（正）と食い違わないか。

    食い違い＝conformancer が **古いプロトコルを前提に**テストしている合図（protocols.py を正として
    `_op_*` と写しを追従させる）。違反メッセージ list を返す（空＝一致）。
    """
    return signature_drift(AsyncFileStore, _PINNED_STORE_SIGNATURES) + signature_drift(
        AsyncFileObject, _PINNED_FILEOBJECT_SIGNATURES
    )


def assert_conformancer_protocol_current() -> None:
    """conformancer の前提 Protocol が protocols.py（正）と一致することを表明する。

    不一致なら `AssertionError`＝conformancer が古い契約を叩いている。protocols.py を正とし、
    `_op_*` の呼び出しと上記シグネチャ写しを新契約へ追従させること。
    """
    errors = conformancer_protocol_drift()
    if errors:
        raise AssertionError(
            "conformancer が古いプロトコルを前提にしている（protocols.py が正）:\n  "
            + "\n  ".join(errors)
        )


async def assert_put_if_absent_concurrency_safe(
    store: object, *, size: int = 1 << 20, stagger: float = 0.001
) -> bytes:
    """**create-only CAS**（`put(key, value, if_match=FileInfo.absent())`）の並行安全性を検査する。

    2 つの writer が同一キーへ同時に create-only put する。**内容を変え**（先行=`b"A"*size`／
    後発=`b"B"*size`）、**大きめの `size`** で窓を広げ、後発を `stagger` 秒遅らせて重なりを作る。
    検証する不変条件（= 製品の必須挙動）:

    1. **ちょうど一方だけ成功**し、他方は `ConflictError`（両方成功＝二重作成／両方失敗＝NG）。
    2. **保存値が成功した側の内容と完全一致**（敗者に上書きされない・torn/混在しない）。

    50 並列「1 つ勝つ」より *何を確認するか* が明確で、**内容で勝者を識別**できる（戻り値＝勝者の
    content＝どちらが優先されたか）。「最小機能だが提供挙動の最小保証（並行安全性）は担保」という
    製品コンセプトを conformance（標準）で機械検証する核。read-only は `put` が
    `UnsupportedOperation` を上げる＝呼ばない。違反は `AssertionError`。
    """
    key = f"_conformance/cc/{uuid.uuid4().hex}"
    with contextlib.suppress(Exception):
        await store.delete(key)  # クリーン初期状態（既存だと先頭から conflict になる）
    first, second = b"A" * size, b"B" * size

    async def _writer(value: bytes, delay: float) -> bytes | None:
        if delay:
            await asyncio.sleep(delay)  # 後発をわずかに遅らせて先行/後発の重なりを作る
        try:
            await store.put(key, value, if_match=FileInfo.absent(key))
            return value  # この writer が作成に成功（＝勝者の内容）
        except ConflictError:
            return None  # 既に作られていた（敗者）

    results = await asyncio.gather(_writer(first, 0.0), _writer(second, stagger))
    winners = [v for v in results if v is not None]
    if len(winners) != 1:
        raise AssertionError(
            f"create-only put 並行安全性違反: 同時 2 本で成功 {len(winners)} 件"
            f"（期待＝ちょうど 1・他方は ConflictError／両方成功なら二重作成）"
        )
    stored = await store.get_or_raise(key)
    if stored != winners[0]:
        raise AssertionError(
            "create-only put 並行安全性違反: 保存値が勝者の内容と不一致"
            "（敗者に上書きされた＝torn write か取り違え）"
        )
    return winners[0]  # 勝者の content（＝どちらが優先されたかを観測できる）


async def assert_put_if_match_concurrency_safe(
    store: object, *, size: int = 1 << 20, stagger: float = 0.001
) -> bytes:
    """**update CAS**（`put(key, value, if_match=<head の FileInfo>)`）の並行安全性を検査する。

    既存値を 1 つ作り、2 writer が **同じ base version**（`head` の FileInfo）を読んでから内容を
    変えて同時に update CAS する。後発を `stagger` 秒遅らせ、大きめ `size` で窓を広げる。不変条件:

    1. **ちょうど一方だけ成功**し、他方は `ConflictError`（lost-update を黙って通さない）。
    2. **保存値が成功した側の内容と完全一致**（敗者に上書きされない）。

    ユーザー原体験（既存値の並行上書きで先勝ち）を conformance で機械検証する核。違反は誤り。
    """
    key = f"_conformance/cc/{uuid.uuid4().hex}"
    with contextlib.suppress(Exception):
        await store.delete(key)
    await store.put(key, b"seed")  # base を作る（両 writer が同じ版を読む）
    base = await store.head(key)
    first, second = b"A" * size, b"B" * size

    async def _writer(value: bytes, delay: float) -> bytes | None:
        if delay:
            await asyncio.sleep(delay)
        try:
            await store.put(key, value, if_match=base)
            return value  # base 版から原子的に更新できた（＝勝者）
        except ConflictError:
            return None  # 版が既に進んでいた（敗者＝lost-update を検出）

    results = await asyncio.gather(_writer(first, 0.0), _writer(second, stagger))
    winners = [v for v in results if v is not None]
    if len(winners) != 1:
        raise AssertionError(
            f"put(if_match=<version>) 並行安全性違反: 同時 2 本で成功 {len(winners)} 件"
            f"（期待＝ちょうど 1・他方は ConflictError／両方成功なら lost-update）"
        )
    stored = await store.get_or_raise(key)
    if stored != winners[0]:
        raise AssertionError(
            "put(if_match=<version>) 並行安全性違反: 保存値が勝者と不一致（敗者に上書きされた）"
        )
    with contextlib.suppress(Exception):
        await store.delete(key)
    return winners[0]


def assert_key_value_store(obj: object) -> None:
    """`obj` が [KeyValueStore] の全メソッドを持つことを表明する。"""
    assert_implements(obj, AsyncKeyValueStore)


def assert_file_store(obj: object) -> None:
    """`obj` が [FileStore]（= KeyValueStore + open_reader/open_writer）を持つことを表明する。"""
    assert_implements(obj, AsyncFileStore)


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
        # 浅い field 展開で記録する（`asdict` は再帰時に dict サブクラスの [FileInfo] を
        # `type(obj)(genexpr)` で再構築しようとして壊れる）。値は dict/list でそのまま JSON 化可。
        report.append({f.name: getattr(step, f.name) for f in fields(step)})

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
