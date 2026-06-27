"""横断的な準拠テスト。

(1) 全 backend が `KeyValueStore` / `FileStore` Protocol のメソッドを揃えているか（存在チェック）、
(2) `FileStoreTester` が辞書ストアをオラクルに対象の挙動（run_light）を差分検証できるか、を確認。
サードパーティ backend も `manystore.conformancer` を import すれば同じ検査を回せる。
"""

import asyncio

import pytest

from manystore import (
    DictFileStore,
    DictKeyValueStore,
    HttpFileStore,
    HttpKeyValueStore,
    LocalFileStore,
    LocalKeyValueStore,
    NatsFileStore,
    NatsObjectKeyValueStore,
    S3FileStore,
    S3KeyValueStore,
)
from manystore.client import RemoteKeyValueStore
from manystore.exceptions import ConflictError, NotFoundError
from manystore.protocols import ABSENT, FileStoreBase, KeyValueStoreBase
from manystore.storage.file import AsyncFileStore
from manystore.storage.kv import AsyncKeyValueStore
from manystore.tools.conformancer import (
    FileStoreTester,
    assert_base_protocol_parity,
    assert_conformancer_protocol_current,
    assert_file_store,
    assert_key_value_store,
    assert_put_if_absent_concurrency_safe,
    assert_put_if_match_concurrency_safe,
    base_protocol_parity_errors,
    conformancer_protocol_drift,
    missing_members,
    required_members,
    save_report,
    signature_drift,
)


def _kvs_instances(tmp_path):
    # 接続はしない（メソッド存在チェックは生成だけで十分）。サーバ越しの RemoteKeyValueStore も
    # 「関係するストア」として roster に含める（get_or_raise 未実装などの取りこぼしを検知する）。
    return [
        DictKeyValueStore(),
        LocalKeyValueStore(tmp_path),
        S3KeyValueStore(bucket="b"),
        NatsObjectKeyValueStore(url="nats://x", bucket="b"),
        HttpKeyValueStore(base_url="http://x"),
        RemoteKeyValueStore("http://x", "ctx"),
    ]


def _file_store_instances(tmp_path):
    return [
        DictFileStore(),
        LocalFileStore(tmp_path),
        S3FileStore(bucket="b"),
        NatsFileStore(url="nats://x", bucket="b"),
        HttpFileStore(base_url="http://x"),
    ]


def test_all_key_value_stores_have_required_methods(tmp_path) -> None:
    for store in _kvs_instances(tmp_path):
        assert_key_value_store(store)  # 欠けていれば AssertionError で backend 名つき


def test_all_file_stores_have_required_methods(tmp_path) -> None:
    # FileStore は KVS + open_reader/open_writer。全 FileStore がそれを満たす。
    for store in _file_store_instances(tmp_path):
        assert_file_store(store)


def test_file_store_requires_io_on_top_of_kvs() -> None:
    # 包含関係の確認: FileStore のメンバ ⊇ KVS のメンバ ＋ open_reader/open_writer。
    kvs = required_members(AsyncKeyValueStore)
    from manystore.storage.file import AsyncFileStore

    fs = required_members(AsyncFileStore)
    assert kvs <= fs
    assert fs - kvs == {"open_reader", "open_writer"}


# ── 挙動契約テストツール（辞書ストアをオラクルに run_light・report に追記） ──


async def test_run_light_local_file_store_matches_oracle(tmp_path) -> None:
    # 辞書ストアを正に LocalFileStore の IO/exists/list_all/iter_all を差分検証。
    tester = FileStoreTester(DictFileStore(), LocalFileStore(tmp_path))
    report: list = []
    await tester.run_light(report)
    assert all(s["passed"] for s in report), report
    assert len(report) == 12  # 観点数
    aspects = {s["aspect"] for s in report}
    assert {"list_all:after_write", "iter_all:after_write"} <= aspects


async def test_run_light_records_state_per_op(tmp_path) -> None:
    # op 毎に「適用後の状態」（iter_all のファイル名・昇順）が返り値とは別に記録される。
    tester = FileStoreTester(DictFileStore(), LocalFileStore(tmp_path))
    report: list = []
    await tester.run_light(report)
    by_aspect = {s["aspect"]: s for s in report}

    # 全ステップが状態を持ち、昇順で reference/target 一致。
    for s in report:
        assert "expected_state" in s and "actual_state" in s
        assert s["expected_state"] == sorted(s["expected_state"])  # 昇順
        assert s["expected_state"] == s["actual_state"]

    # クリーン直後の missing 観点は空状態、書き込み後はキーが状態に現れる（副作用の検証）。
    assert by_aspect["exists:missing"]["actual_state"] == []
    written = by_aspect["iter_all:after_write"]["actual_state"]
    assert len(written) == 1 and written[0].endswith("/a")


async def test_run_light_dict_self_consistent() -> None:
    # 正=対象=辞書ストアなら全観点一致（ツールの健全性）。
    tester = FileStoreTester(DictFileStore(), DictFileStore())
    report: list = []
    await tester.run_light(report)
    assert all(s["passed"] for s in report)


async def test_run_light_detects_divergence(tmp_path) -> None:
    # 壊れた実装（書いても保存されない）は観点が fail する＝ツールが差分を検出する。
    class _NoopWriter:
        async def write(self, data):
            return len(data)

        async def close(self): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc): ...

    broken = LocalFileStore(tmp_path)

    async def open_writer(filename):  # 書き込みを握り潰す壊れた open_writer
        return _NoopWriter()

    broken.open_writer = open_writer
    tester = FileStoreTester(DictFileStore(), broken)
    report: list = []
    await tester.run_light(report)
    assert any(
        not s["passed"] for s in report
    )  # 書けていない→read/exists/list がオラクルと食い違う


async def test_run_light_report_is_external_and_saves(tmp_path) -> None:
    import json

    # ツールはレポートを保持しない＝呼び出し側の list に操作順で追記される。
    tester = FileStoreTester(DictFileStore(), LocalFileStore(tmp_path))
    report: list = []
    await tester.run_light(report)
    assert report[0]["op"] == "exists"  # 操作順・op/args/expected が残る（リプレイ素材）
    assert "expected" in report[0]
    assert "expected_state" in report[0] and "actual_state" in report[0]  # 状態も保存される
    out = tmp_path / "report.json"
    save_report(report, out)
    assert json.loads(out.read_text(encoding="utf-8"))[0]["aspect"] == "exists:missing"


def test_conformance_detects_missing_method() -> None:
    # メソッドが欠けた偽実装は不足が検出される（ツール自体の健全性）。
    class _Broken:
        async def put(self, key, value): ...

    missing = missing_members(_Broken(), AsyncKeyValueStore)
    assert "get_or_raise" in missing
    assert "iter_all" in missing
    with pytest.raises(AssertionError):
        assert_key_value_store(_Broken())


async def test_base_enforces_full_protocol_at_instantiation() -> None:
    # KeyValueStoreBase の primitive（put/get_or_raise/iter_all/exists/delete/connect/aclose）を
    # 一部でも実装し忘れたストアは、呼ぶ前に **インスタンス化時点で TypeError**＝部分実装が黙って
    # Protocol を破る（M043 のドリフト）のを fail-loud に防ぐ。get_or_raise だけ実装した旧来 OK な
    # 部分実装も、いまは未実装の primitive が残るため生成できない。
    class _ForgotMost(KeyValueStoreBase):
        async def get_or_raise(self, key):  # 残り primitive を実装していない
            raise NotFoundError(key)

    with pytest.raises(TypeError):
        _ForgotMost()

    # primitive を全実装したサブクラスは生成でき、get/list_all/cp/mv は基底の既定実装から得られる。
    class _Ok(KeyValueStoreBase):
        async def put(self, key, value):
            return {"filename": key, "size": len(value)}

        async def get_or_raise(self, key):
            raise NotFoundError(key)

        async def iter_all(self, limit=None, prefix=""):
            return
            yield  # 空ジェネレータ

        async def exists(self, key):
            return False

        async def delete(self, key): ...
        async def connect(self): ...
        async def aclose(self): ...

    await _assert_defaults(_Ok())


async def _assert_defaults(store) -> None:
    assert await store.get("missing", default=b"d") == b"d"  # get は get_or_raise から
    assert await store.list_all() == []  # list_all は iter_all から


# ── 基底↔Protocol parity（M043: 契約と既定実装の lockstep を機械的に保証） ──


def test_kvs_base_matches_protocol() -> None:
    # KeyValueStoreBase が AsyncKeyValueStore を網羅し、全メソッドのシグネチャが一致する。
    assert base_protocol_parity_errors(KeyValueStoreBase, AsyncKeyValueStore) == []
    assert_base_protocol_parity(KeyValueStoreBase, AsyncKeyValueStore)


def test_file_store_base_matches_protocol() -> None:
    # FileStoreBase は AsyncFileStore（= KVS + open_reader/open_writer）を網羅・シグネチャ一致。
    assert base_protocol_parity_errors(FileStoreBase, AsyncFileStore) == []
    assert_base_protocol_parity(FileStoreBase, AsyncFileStore)


def test_conformancer_assumes_current_protocol() -> None:
    # conformancer（FileStoreTester/_OPS）が前提とする Protocol メソッドのシグネチャが protocols.py
    # （正）と一致する。食い違えば conformancer が古い契約を叩いている＝_op_* と写しの追従が必要。
    assert conformancer_protocol_drift() == []
    assert_conformancer_protocol_current()


def test_signature_drift_detects_stale_assumption() -> None:
    # ツール自体の健全性: protocols.py が前提（写し）と食い違えば drift として検出する。
    stale = {"exists": "(self, key: str, extra: int) -> bool"}  # 実際の Protocol には無い引数
    drift = signature_drift(AsyncFileStore, stale)
    assert any("exists" in e for e in drift)

    removed = {"no_such_method": "(self) -> None"}  # 改廃されたメンバ
    assert any("no_such_method" in e for e in signature_drift(AsyncFileStore, removed))


def test_parity_detects_missing_and_signature_drift() -> None:
    # ツール自体の健全性: 網羅漏れとシグネチャ不一致の両方を検出する。
    class _PartialBase:
        async def put(self, key, value): ...  # 他の Protocol メンバが無い

    errors = base_protocol_parity_errors(_PartialBase, AsyncKeyValueStore)
    assert any("get_or_raise" in e for e in errors)  # 網羅漏れを検出

    class _SigDrift(KeyValueStoreBase):  # exists の引数名を変える（シグネチャ drift）
        async def exists(self, name): ...  # type: ignore[override]

    drift = base_protocol_parity_errors(_SigDrift, AsyncKeyValueStore)
    assert any("exists" in e and "シグネチャ" in e for e in drift)


# ── 並行安全性（M046: put を持つストアの必須挙動を conformance で機械検証） ──
#
# conditional put（`put(if_match=...)`）の並行安全性を **実ストア経由**で検証する。create 競合は
# `if_match=ABSENT`、更新の lost-update 検出は `if_match=<head の FileInfo>`。チェッカ自身の健全性
# （衝突を検出して落とせるか）は、**故意に壊した TOCTOU ストア**を 1 つだけ残して担保する
# （正しい実ストアは原理的に競合を起こせないため、否定テストには壊したダブルが要る）。


class _RacyCreateDict:
    """チェッカの否定テスト専用＝**故意に TOCTOU で壊した** create-only ストア。

    `put(if_match=ABSENT)` の存在確認を保持したまま `await` で yield し、その隙に他コルーチンが
    作成できる＝両方が「無い」と判断して二重作成しうる（lost-update）。これでチェッカが衝突を
    検出して落とせることを確認する（実ストアでは起こせない壊れ方を意図的に作る）。
    """

    def __init__(self) -> None:
        self._d: dict[str, bytes] = {}

    async def delete(self, key: str) -> None:
        self._d.pop(key, None)

    async def get_or_raise(self, key: str) -> bytes:
        try:
            return self._d[key]
        except KeyError:
            raise NotFoundError(key) from None

    async def put(self, key: str, value: bytes, *, if_match=None):
        exists = key in self._d
        await asyncio.sleep(0)  # 存在確認を保持したまま yield＝TOCTOU の窓
        if if_match is not None and if_match.is_absent() and exists:
            raise ConflictError(key)
        self._d[key] = value
        return {"filename": key, "size": len(value)}


async def test_concurrency_checker_passes_for_real_dict_store() -> None:
    # 実ストア（DictKeyValueStore）の create-only CAS は「一方成功・保存値=勝者」を満たす＝通る。
    # 後発を stagger で遅らせるので先行（b"A"）が勝つ＝どちらが優先されたかを内容で識別できる。
    winner = await assert_put_if_absent_concurrency_safe(DictKeyValueStore(), size=256)
    assert winner == b"A" * 256  # 先行 writer が優先された


async def test_concurrency_checker_fails_on_collision() -> None:
    # 故意に壊した TOCTOU ストア＝両 writer が「無い」と判断し両方作成（二重作成）。stagger=0 で
    # 重なりを作り成功 2 件にすると AssertionError で落とす（= 衝突でテストを失敗させられる確認）。
    with pytest.raises(AssertionError, match="並行安全性違反"):
        await assert_put_if_absent_concurrency_safe(_RacyCreateDict(), size=256, stagger=0.0)


# ── 実 backend の必須挙動（ストア経由）: create 競合 ＋ 更新 lost-update を実ストアで検証 ──


async def test_dict_put_if_absent_concurrent_winner_is_intact() -> None:
    await assert_put_if_absent_concurrency_safe(DictKeyValueStore())


async def test_local_put_if_absent_concurrent_winner_is_intact(tmp_path) -> None:
    await assert_put_if_absent_concurrency_safe(LocalKeyValueStore(tmp_path))


async def test_dict_put_if_match_concurrent_winner_is_intact() -> None:
    await assert_put_if_match_concurrency_safe(DictKeyValueStore())


async def test_local_put_if_match_concurrent_winner_is_intact(tmp_path) -> None:
    await assert_put_if_match_concurrency_safe(LocalKeyValueStore(tmp_path))


# ── conditional put の単体挙動（実ストア経由） ──


async def test_dict_conditional_put_unit(tmp_path) -> None:
    store = DictKeyValueStore()
    info = await store.put("k", b"v1")
    assert (info["filename"], info["size"]) == ("k", 2)  # put の戻りは filename/size
    # create-only: 2 回目は ConflictError
    with pytest.raises(ConflictError):
        await store.put("k", b"v2", if_match=ABSENT)
    # head で version を読み、一致すれば update できる
    meta = await store.head("k")
    assert meta["size"] == 2 and meta["etag"] is not None
    await store.put("k", b"v3xx", if_match=meta)
    assert await store.get_or_raise("k") == b"v3xx"
    # 古い version での update は ConflictError（lost-update 検出）
    with pytest.raises(ConflictError):
        await store.put("k", b"zzzz", if_match=meta)


async def test_local_conditional_put_unit(tmp_path) -> None:
    store = LocalKeyValueStore(tmp_path)
    await store.put("k", b"v1")
    with pytest.raises(ConflictError):
        await store.put("k", b"v2", if_match=ABSENT)
    meta = await store.head("k")
    assert meta["etag"] is not None and meta["modified_at"] is not None
    await store.put("k", b"v3xx", if_match=meta)
    assert await store.get_or_raise("k") == b"v3xx"
    with pytest.raises(ConflictError):
        await store.put("k", b"zzzz", if_match=meta)


async def test_head_or_absent_upsert() -> None:
    # head_or_absent の戻りをそのまま if_match に渡す＝create-or-update を一発で CAS 付きに。
    store = DictKeyValueStore()
    # 不在 → is_absent() な FileInfo（ABSENT）が返り、create CAS として成功（新規作成）。
    cond = await store.head_or_absent("k")
    assert cond.is_absent()
    await store.put("k", b"v1", if_match=cond)
    assert await store.get_or_raise("k") == b"v1"
    # 存在 → 実 FileInfo が返り、update CAS として成功（版一致）。
    cond = await store.head_or_absent("k")
    assert not cond.is_absent() and cond["etag"] is not None
    await store.put("k", b"v2", if_match=cond)
    assert await store.get_or_raise("k") == b"v2"
    # 古い cond（版が進んだ後）での upsert は ConflictError（並行変化を検出）。
    with pytest.raises(ConflictError):
        await store.put("k", b"v3", if_match=cond)
