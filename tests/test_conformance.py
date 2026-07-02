"""横断的な準拠テスト。

(1) 全 backend が Store（値 API＋IO API）の Protocol メソッドを揃えているか（存在チェック）、
(2) `StoreTester` が辞書ストアをオラクルに対象の挙動（run_light）を差分検証できるか、を確認。
サードパーティ backend も `manystore.conformancer` を import すれば同じ検査を回せる。
"""

import asyncio

import pytest

from manystore import (
    DictStore,
    DownloadCache,
    HttpStore,
    LocalStore,
    NatsStore,
    S3Store,
)
from manystore.client import RemoteStore
from manystore.spec import (
    AsyncBufferedStore,
    AsyncStreamingStore,
    BufferedStoreBase,
    FileInfo,
    StreamingStoreBase,
)
from manystore.spec.conformancer import (
    ABSOLUTE_CONTRACTS,
    StoreTester,
    assert_base_protocol_parity,
    assert_buffered_store,
    assert_concrete_store_signatures,
    assert_concurrent_delete_safe,
    assert_concurrent_overwrite_atomic,
    assert_conformancer_protocol_current,
    assert_contract_catalog_current,
    assert_fail_loud_propagation,
    assert_put_if_absent_concurrency_safe,
    assert_store,
    assert_writer_aborts_on_error,
    base_protocol_parity_errors,
    concrete_store_signature_errors,
    conformancer_protocol_drift,
    differential_contract_aspects,
    missing_members,
    required_members,
    save_report,
    scaffold_backend,
    signature_drift,
)
from manystore.spec.exceptions import ConflictError, NotFoundError
from manystore.storage.surfaces.safe import SafeStore


def _kvs_instances(tmp_path):
    # 接続はしない（メソッド存在チェックは生成だけで十分）。サーバ越しの RemoteStore も
    # 「関係するストア」として roster に含める（get_or_raise 未実装などの取りこぼしを検知する）。
    return [
        DictStore(),
        LocalStore(tmp_path),
        S3Store(bucket="b"),
        NatsStore(url="nats://x", bucket="b"),
        HttpStore(base_url="http://x"),
        RemoteStore("http://x", "ctx"),
    ]


def _store_instances(tmp_path):
    return [
        DictStore(),
        LocalStore(tmp_path),
        S3Store(bucket="b"),
        NatsStore(url="nats://x", bucket="b"),
        HttpStore(base_url="http://x"),
    ]


def test_all_buffered_stores_have_required_methods(tmp_path) -> None:
    for store in _kvs_instances(tmp_path):
        assert_buffered_store(store)  # 欠けていれば AssertionError で backend 名つき


def test_all_stores_have_required_methods(tmp_path) -> None:
    # full Store は KVS + open_reader/open_writer。全 Store がそれを満たす。
    for store in _store_instances(tmp_path):
        assert_store(store)


def test_store_requires_io_on_top_of_kvs() -> None:
    # 包含関係の確認: full Store のメンバ ⊇ KVS のメンバ ＋ open_reader/open_writer。
    kvs = required_members(AsyncBufferedStore)
    fs = required_members(AsyncStreamingStore)
    assert kvs <= fs
    assert fs - kvs == {"open_reader", "open_writer"}


# ── 挙動契約テストツール（辞書ストアをオラクルに run_light・report に追記） ──


async def test_run_light_local_store_matches_oracle(tmp_path) -> None:
    # 辞書ストアを正に LocalStore の IO/exists/list_all/iter_all を差分検証。
    tester = StoreTester(DictStore(), LocalStore(tmp_path))
    report: list = []
    await tester.run_light(report)
    assert all(s["passed"] for s in report), report
    assert len(report) == 12  # 観点数
    aspects = {s["aspect"] for s in report}
    assert {"list_all:after_write", "iter_all:after_write"} <= aspects


async def test_run_light_records_state_per_op(tmp_path) -> None:
    # op 毎に「適用後の状態」（iter_all のファイル名・昇順）が返り値とは別に記録される。
    tester = StoreTester(DictStore(), LocalStore(tmp_path))
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
    tester = StoreTester(DictStore(), DictStore())
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

    broken = LocalStore(tmp_path)

    async def open_writer(filename):  # 書き込みを握り潰す壊れた open_writer
        return _NoopWriter()

    broken.open_writer = open_writer
    tester = StoreTester(DictStore(), broken)
    report: list = []
    await tester.run_light(report)
    assert any(
        not s["passed"] for s in report
    )  # 書けていない→read/exists/list がオラクルと食い違う


# ── run_middle（細かい挙動契約・差分検証）＋ writer all-or-nothing 絶対契約（M065） ──


async def test_run_middle_local_store_matches_oracle(tmp_path) -> None:
    # 辞書ストアを正に LocalStore の delete/冪等/複数キー/read 境界/overwrite 縮小を差分検証。
    tester = StoreTester(DictStore(), LocalStore(tmp_path))
    report: list = []
    await tester.run_middle(report)
    assert all(s["passed"] for s in report), report
    aspects = {s["aspect"] for s in report}
    assert {"delete:missing_idempotent", "list_all:multi_key", "overwrite:shrink"} <= aspects


async def test_run_middle_dict_self_consistent() -> None:
    # 正=対象=辞書ストアなら run_middle も全観点一致（ツールの健全性）。
    tester = StoreTester(DictStore(), DictStore())
    report: list = []
    await tester.run_middle(report)
    assert all(s["passed"] for s in report)


# ── run_heavy（規模・境界の挙動契約・差分検証・M065） ──


async def test_run_heavy_local_store_matches_oracle(tmp_path) -> None:
    # 辞書ストアを正に LocalStore の大容量/分割 read/多キー/連続 overwrite を差分検証。
    tester = StoreTester(DictStore(), LocalStore(tmp_path))
    report: list = []
    await tester.run_heavy(report)
    assert all(s["passed"] for s in report), report
    aspects = {s["aspect"] for s in report}
    assert {"heavy:read_large_full", "heavy:read_segments", "heavy:read_after_regrow"} <= aspects


async def test_run_heavy_dict_self_consistent() -> None:
    # 正=対象=辞書ストアなら run_heavy も全観点一致（ツールの健全性）。
    tester = StoreTester(DictStore(), DictStore())
    report: list = []
    await tester.run_heavy(report)
    assert all(s["passed"] for s in report)


async def test_run_heavy_detects_truncating_reader(tmp_path) -> None:
    # 大容量を 1 チャンクに切り詰める壊れた reader は heavy（分割 read/全長 read）で発覚する。
    class _TruncReader:
        def __init__(self, data: bytes) -> None:
            self._data = data

        async def read(self, n: int = -1) -> bytes:
            chunk, self._data = self._data[:64], b""  # 64 バイトで頭打ち（残りを落とす）
            return chunk[:n] if n is not None and n >= 0 else chunk

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc): ...

    broken = LocalStore(tmp_path)
    inner_open_reader = broken.open_reader

    async def open_reader(filename):  # noqa: ANN001  実 reader の中身を 64B 截断で包む
        r = await inner_open_reader(filename)
        async with r:
            data = await r.read(-1)
        return _TruncReader(data)

    broken.open_reader = open_reader
    tester = StoreTester(DictStore(), broken)
    report: list = []
    await tester.run_heavy(report)
    assert any(not s["passed"] for s in report)  # 大容量 read がオラクルと食い違う


# writer all-or-nothing の Dict/Local/Remote/実 backend 横断検証は集約ハーネス
# （test_conformance_matrix.test_writer_aborts_on_error）に移設。ここはツールの牙のみ残す。


async def test_writer_abort_contract_catches_committing_writer(tmp_path) -> None:
    # 契約の牙: 例外経路でも put する壊れた writer は assert_writer_aborts_on_error で落ちる。
    class _CommitOnExitWriter:
        def __init__(self, store, key):
            self._store, self._key, self._buf = store, key, b""

        async def write(self, data):
            self._buf += data
            return len(data)

        async def close(self): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            await self._store.put(self._key, self._buf)  # 例外経路でも確定＝契約違反

    broken = LocalStore(tmp_path)

    async def open_writer(filename):
        return _CommitOnExitWriter(broken, filename)

    broken.open_writer = open_writer
    with pytest.raises(AssertionError, match="all-or-nothing"):
        await assert_writer_aborts_on_error(broken)


# ── 非CAS 並行契約（無条件上書きの原子性・並行 delete/get 安全・M065 残） ──


async def test_concurrent_overwrite_atomic_dict_passes() -> None:
    # 健全なストア（dict）は並行上書きでも最終値が完全な A/B＝契約を満たす。
    last = await assert_concurrent_overwrite_atomic(DictStore(), size=256, rounds=2)
    assert len(last) == 256 and len(set(last)) == 1  # 単一バイトの完全値（torn でない）


async def test_concurrent_overwrite_atomic_local_passes(tmp_path) -> None:
    await assert_concurrent_overwrite_atomic(LocalStore(tmp_path), size=256, rounds=2)


async def test_concurrent_overwrite_atomic_catches_torn_writer() -> None:
    # 契約の牙: A/B を連結して torn な値を確定する壊れた put は原子性違反で落ちる。
    class _TornStore(DictStore):
        async def put(self, key, value, *, if_match=None):  # noqa: ANN001
            # 並行 2 本目の put で既存値に追記＝混在（torn）した値を残す。
            prev = await self.get(key, default=b"")
            return await super().put(key, prev + value if prev else value)

    with pytest.raises(AssertionError, match="原子性違反"):
        await assert_concurrent_overwrite_atomic(_TornStore(), size=256, rounds=1)


async def test_concurrent_delete_safe_dict_passes() -> None:
    await assert_concurrent_delete_safe(DictStore())


async def test_concurrent_delete_safe_local_passes(tmp_path) -> None:
    await assert_concurrent_delete_safe(LocalStore(tmp_path))


async def test_concurrent_delete_safe_catches_non_deleting_store() -> None:
    # 契約の牙: delete を握り潰して何も消さない壊れたストアは「完了後 不在」で落ちる。
    class _NoDeleteStore(DictStore):
        async def delete(self, key):  # noqa: ANN001  delete を no-op 化（残存させる）
            return None

    with pytest.raises(AssertionError, match="残存"):
        await assert_concurrent_delete_safe(_NoDeleteStore())


# ── run_full（差分 light+middle+heavy ＋ 絶対契約を 1 レポートへ集約・M065 残） ──


async def test_run_full_dict_self_consistent() -> None:
    # 健全なストアは差分も絶対契約も全て passed＝run_full の全観点が緑。
    tester = StoreTester(DictStore(), DictStore())
    report: list = []
    await tester.run_full(report)
    assert all(s["passed"] for s in report), [s for s in report if not s["passed"]]
    aspects = {s["aspect"] for s in report}
    # 差分（各レベル）＋絶対契約（非CAS 並行を含む）が 1 レポートに揃う。
    assert {"exists:missing", "delete:missing_idempotent", "heavy:read_segments"} <= aspects
    assert {
        "absolute:writer.all_or_nothing",
        "absolute:put.create_only.concurrency",
        "absolute:concurrent.overwrite_atomic",
        "absolute:concurrent.delete_safe",
    } <= aspects


async def test_run_full_local_matches_oracle(tmp_path) -> None:
    tester = StoreTester(DictStore(), LocalStore(tmp_path))
    report: list = []
    await tester.run_full(report)
    assert all(s["passed"] for s in report), [s for s in report if not s["passed"]]


async def test_run_full_records_absolute_violation_without_raising() -> None:
    # run_full は絶対契約違反でも止めず passed=False で記録する（全契約を回し切る）。
    class _NoDeleteStore(DictStore):
        async def delete(self, key):  # noqa: ANN001
            return None

    tester = StoreTester(DictStore(), _NoDeleteStore())
    report: list = []
    await tester.run_full(report)  # 例外を投げない
    violated = [s for s in report if not s["passed"]]
    assert any(s["aspect"] == "absolute:concurrent.delete_safe" for s in violated)


# ── fail-loud 契約（fault-injection で下層障害の握り潰しを横断検知・M065 step2） ──


@pytest.mark.parametrize(
    "make_store",
    [
        pytest.param(lambda inner: inner, id="base_duality"),
        pytest.param(lambda inner: SafeStore(inner), id="safe"),
        pytest.param(lambda inner: DownloadCache(inner), id="download_cache"),
    ],
)
async def test_fail_loud_propagation_contract(make_store) -> None:
    # 下層（FaultInjectingStore）の InjectedFault を握り潰さず伝播すること。基底の
    # get duality（NotFoundError 以外を default に化けさせない）も identity で同時に検証する。
    await assert_fail_loud_propagation(make_store)


async def test_fail_loud_contract_catches_swallowing_wrapper() -> None:
    # 契約の牙: exists を握り潰す（M055 と同型の）壊れた wrapper は契約で落ちる。
    class _SwallowExists(BufferedStoreBase):
        def __init__(self, inner):
            self._inner = inner

        async def connect(self):
            await self._inner.connect()

        async def aclose(self):
            await self._inner.aclose()

        async def put(self, key, value, *, if_match=None):
            return await self._inner.put(key, value, if_match=if_match)

        async def get_or_raise(self, key):
            return await self._inner.get_or_raise(key)

        async def exists(self, key):
            try:
                return await self._inner.exists(key)
            except Exception:  # 障害を握り潰して False＝fail-loud 違反
                return False

        async def delete(self, key):
            await self._inner.delete(key)

        async def iter_all(self, limit=None, prefix=""):
            async for x in self._inner.iter_all(limit, prefix):
                yield x

    with pytest.raises(AssertionError, match="exists"):
        await assert_fail_loud_propagation(lambda inner: _SwallowExists(inner))


# ── 挙動契約カタログ（仕様書の正本・M065 step3） ──


def test_contract_catalog_is_current() -> None:
    # カタログ（仕様書の正本）の各絶対契約が実在の assert 関数を指す（仕様だけでテスト無しを防ぐ）。
    assert_contract_catalog_current()


def test_contract_catalog_has_expected_absolute_contracts() -> None:
    # 監査由来の絶対契約が漏れなくカタログに載っていること（新規追加時の登録漏れ検知）。
    ids = {c.id for c in ABSOLUTE_CONTRACTS}
    assert {
        "writer.all_or_nothing",
        "put.create_only.concurrency",
        "put.update_cas.concurrency",
        "errors.fail_loud",
        "concurrent.overwrite_atomic",
        "concurrent.delete_safe",
    } <= ids


async def test_differential_aspects_derived_from_runs() -> None:
    # 差分観点は run_light/run_middle の実行から導出される（doc が実態と乖離しない）。
    pairs = await differential_contract_aspects()
    levels = {lv for lv, _ in pairs}
    assert levels == {"light", "middle", "heavy"}
    aspects = {a for _, a in pairs}
    assert {
        "exists:missing",
        "delete:missing_idempotent",
        "overwrite:shrink",
        "heavy:read_segments",
    } <= aspects


# ── 契約カタログ→backend 雛形の生成（北極星④・M065 step4） ──


@pytest.mark.parametrize(
    ("kind", "base_name"), [("file", "StreamingStoreBase"), ("kv", "BufferedStoreBase")]
)
async def test_scaffold_compiles_and_stubs_raise(kind: str, base_name: str) -> None:
    # 雛形は compile でき、未実装 primitive を呼ぶと NotImplementedError（＝実装の TODO が loud）。
    code = scaffold_backend("MyStore", kind=kind)
    assert f"class MyStore({base_name})" in code
    ns: dict = {}
    exec(compile(code, "<scaffold>", "exec"), ns)  # noqa: S102  生成コードの健全性検査
    store = ns["MyStore"]()  # 全 abstract が stub 済＝インスタンス化できる
    with pytest.raises(NotImplementedError):
        await store.exists("k")


def test_scaffold_lists_absolute_contracts() -> None:
    # 雛形ヘッダに「満たすべき絶対契約」が列挙される（契約一覧＝実装の TODO）。
    code = scaffold_backend("X", kind="file")
    for c in ABSOLUTE_CONTRACTS:
        assert c.id in code


def test_scaffold_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        scaffold_backend("X", kind="bogus")


async def test_run_light_report_is_external_and_saves(tmp_path) -> None:
    import json

    # ツールはレポートを保持しない＝呼び出し側の list に操作順で追記される。
    tester = StoreTester(DictStore(), LocalStore(tmp_path))
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

    missing = missing_members(_Broken(), AsyncBufferedStore)
    assert "get_or_raise" in missing
    assert "iter_all" in missing
    with pytest.raises(AssertionError):
        assert_buffered_store(_Broken())


async def test_base_enforces_full_protocol_at_instantiation() -> None:
    # BufferedStoreBase の primitive（put/get_or_raise/iter_all/exists/delete/connect/aclose）を
    # 一部でも実装し忘れたストアは、呼ぶ前に **インスタンス化時点で TypeError**＝部分実装が黙って
    # Protocol を破る（M043 のドリフト）のを fail-loud に防ぐ。get_or_raise だけ実装した旧来 OK な
    # 部分実装も、いまは未実装の primitive が残るため生成できない。
    class _ForgotMost(BufferedStoreBase):
        async def get_or_raise(self, key):  # 残り primitive を実装していない
            raise NotFoundError(key)

    with pytest.raises(TypeError):
        _ForgotMost()

    # primitive を全実装したサブクラスは生成でき、get/list_all/cp/mv は基底の既定実装から得られる。
    class _Ok(BufferedStoreBase):
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
    # BufferedStoreBase が AsyncBufferedStore を網羅し、全メソッドのシグネチャが一致する。
    assert base_protocol_parity_errors(BufferedStoreBase, AsyncBufferedStore) == []
    assert_base_protocol_parity(BufferedStoreBase, AsyncBufferedStore)


def test_store_base_matches_protocol() -> None:
    # StreamingStoreBase は AsyncStreamingStore（= KVS + open_reader/open_writer）を網羅＆一致。
    assert base_protocol_parity_errors(StreamingStoreBase, AsyncStreamingStore) == []
    assert_base_protocol_parity(StreamingStoreBase, AsyncStreamingStore)


def test_conformancer_assumes_current_protocol() -> None:
    # conformancer（StoreTester/_OPS）が前提とする Protocol メソッドのシグネチャが protocols.py
    # （正）と一致する。食い違えば conformancer が古い契約を叩いている＝_op_* と写しの追従が必要。
    assert conformancer_protocol_drift() == []
    assert_conformancer_protocol_current()


def test_signature_drift_detects_stale_assumption() -> None:
    # ツール自体の健全性: protocols.py が前提（写し）と食い違えば drift として検出する。
    stale = {"exists": "(self, key: str, extra: int) -> bool"}  # 実際の Protocol には無い引数
    drift = signature_drift(AsyncStreamingStore, stale)
    assert any("exists" in e for e in drift)

    removed = {"no_such_method": "(self) -> None"}  # 改廃されたメンバ
    assert any("no_such_method" in e for e in signature_drift(AsyncStreamingStore, removed))


def test_parity_detects_missing_and_signature_drift() -> None:
    # ツール自体の健全性: 網羅漏れとシグネチャ不一致の両方を検出する。
    class _PartialBase:
        async def put(self, key, value): ...  # 他の Protocol メンバが無い

    errors = base_protocol_parity_errors(_PartialBase, AsyncBufferedStore)
    assert any("get_or_raise" in e for e in errors)  # 網羅漏れを検出

    class _SigDrift(BufferedStoreBase):  # exists の引数名を変える（シグネチャ drift）
        async def exists(self, name): ...  # type: ignore[override]

    drift = base_protocol_parity_errors(_SigDrift, AsyncBufferedStore)
    assert any("exists" in e and "シグネチャ" in e for e in drift)


def test_concrete_store_signatures_tolerate_return_narrowing() -> None:
    # ツール自体の健全性: concrete 実装の署名検証は **戻り注釈の narrowing を許容**しつつ
    # **パラメータ drift は捕える**。BufferedStoreBase は戻り注釈まで一致＝当然 OK。
    assert concrete_store_signature_errors(BufferedStoreBase, AsyncBufferedStore) == []
    assert_concrete_store_signatures(BufferedStoreBase, AsyncBufferedStore)

    from collections.abc import AsyncIterable, AsyncIterator

    from manystore.spec import FileInfo

    class _NarrowedReturn(
        BufferedStoreBase
    ):  # 戻り注釈だけ narrowing（AsyncIterable→AsyncIterator）
        async def iter_all(  # type: ignore[override]
            self, limit: int | None = None, prefix: str = ""
        ) -> AsyncIterator[FileInfo]:
            yield FileInfo(filename="x", size=0)

    # 全 backend と同じ narrowing は許容＝違反ゼロ（strict base parity ならここを誤検出する）。
    assert concrete_store_signature_errors(_NarrowedReturn, AsyncBufferedStore) == []

    class _ParamDrift(BufferedStoreBase):  # put の if_match キーワードを落とす（実害ある drift）
        async def put(self, key: str, value: bytes) -> FileInfo:  # type: ignore[override]
            return FileInfo(filename=key, size=len(value))

    errors = concrete_store_signature_errors(_ParamDrift, AsyncBufferedStore)
    assert any("put" in e for e in errors)  # パラメータ drift は loud に捕える
    assert AsyncIterable is not AsyncIterator  # narrowing は型として別物（共変・LSP 安全）


# ── 並行安全性（M046: put を持つストアの必須挙動を conformance で機械検証） ──
#
# conditional put（`put(if_match=...)`）の並行安全性を **実ストア経由**で検証する。create 競合は
# `if_match=FileInfo.absent()`、更新は `if_match=<head の FileInfo>`。チェッカの健全性
# （衝突を検出して落とせるか）は、**故意に壊した TOCTOU ストア**を 1 つだけ残して担保する
# （正しい実ストアは原理的に競合を起こせないため、否定テストには壊したダブルが要る）。


class _RacyCreateDict:
    """チェッカの否定テスト専用＝**故意に TOCTOU で壊した** create-only ストア。

    create-only put の存在確認を保持したまま `await` で yield し、その隙に他コルーチンが
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
    # 実ストア（DictStore）の create-only CAS は「一方成功・保存値=勝者」を満たす＝通る。
    # 後発を stagger で遅らせるので先行（b"A"）が勝つ＝どちらが優先されたかを内容で識別できる。
    winner = await assert_put_if_absent_concurrency_safe(DictStore(), size=256)
    assert winner == b"A" * 256  # 先行 writer が優先された


async def test_concurrency_checker_fails_on_collision() -> None:
    # 故意に壊した TOCTOU ストア＝両 writer が「無い」と判断し両方作成（二重作成）。stagger=0 で
    # 重なりを作り成功 2 件にすると AssertionError で落とす（= 衝突でテストを失敗させられる確認）。
    with pytest.raises(AssertionError, match="並行安全性違反"):
        await assert_put_if_absent_concurrency_safe(_RacyCreateDict(), size=256, stagger=0.0)


# 並行 CAS（create-only / update）の Dict/Local/Remote/実 backend 横断検証は集約ハーネス
# （test_conformance_matrix.test_put_if_absent_concurrency / test_put_if_match_concurrency）に移設。
# ここはチェッカ自身の健全性（winner 識別・衝突で落ちる牙）だけを残す（上記 2 テスト）。


# ── conditional put の単体挙動（実ストア経由） ──


async def test_dict_conditional_put_unit(tmp_path) -> None:
    store = DictStore()
    info = await store.put("k", b"v1")
    assert (info["filename"], info["size"]) == ("k", 2)  # put の戻りは filename/size
    # create-only: 2 回目は ConflictError
    with pytest.raises(ConflictError):
        await store.put("k", b"v2", if_match=FileInfo.absent("k"))
    # head で version を読み、一致すれば update できる
    meta = await store.head("k")
    assert meta["size"] == 2 and meta["etag"] is not None
    await store.put("k", b"v3xx", if_match=meta)
    assert await store.get_or_raise("k") == b"v3xx"
    # 古い version での update は ConflictError（lost-update 検出）
    with pytest.raises(ConflictError):
        await store.put("k", b"zzzz", if_match=meta)


async def test_local_conditional_put_unit(tmp_path) -> None:
    store = LocalStore(tmp_path)
    await store.put("k", b"v1")
    with pytest.raises(ConflictError):
        await store.put("k", b"v2", if_match=FileInfo.absent("k"))
    meta = await store.head("k")
    assert meta["etag"] is not None and meta["modified_at"] is not None
    await store.put("k", b"v3xx", if_match=meta)
    assert await store.get_or_raise("k") == b"v3xx"
    with pytest.raises(ConflictError):
        await store.put("k", b"zzzz", if_match=meta)


async def test_head_or_absent_upsert() -> None:
    # head_or_absent の戻りをそのまま if_match に渡す＝create-or-update を一発で CAS 付きに。
    store = DictStore()
    # 不在 → is_absent() な FileInfo が返り、create CAS として成功（新規作成）。
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
