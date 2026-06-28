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
    DownloadCache,
    HttpFileStore,
    HttpKeyValueStore,
    KeyValueFileStore,
    LocalFileStore,
    LocalKeyValueStore,
    NatsFileStore,
    NatsObjectKeyValueStore,
    S3FileStore,
    S3KeyValueStore,
    SafeKeyValueStore,
)
from manystore.client import RemoteKeyValueStore
from manystore.exceptions import ConflictError, NotFoundError
from manystore.protocols import FileInfo, FileStoreBase, KeyValueStoreBase
from manystore.storage.file import AsyncFileStore
from manystore.storage.kv import AsyncKeyValueStore
from manystore.tools.conformancer import (
    ABSOLUTE_CONTRACTS,
    FileStoreTester,
    assert_base_protocol_parity,
    assert_concrete_store_signatures,
    assert_conformancer_protocol_current,
    assert_contract_catalog_current,
    assert_fail_loud_propagation,
    assert_file_store,
    assert_key_value_store,
    assert_put_if_absent_concurrency_safe,
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


# ── run_middle（細かい挙動契約・差分検証）＋ writer all-or-nothing 絶対契約（M065） ──


async def test_run_middle_local_file_store_matches_oracle(tmp_path) -> None:
    # 辞書ストアを正に LocalFileStore の delete/冪等/複数キー/read 境界/overwrite 縮小を差分検証。
    tester = FileStoreTester(DictFileStore(), LocalFileStore(tmp_path))
    report: list = []
    await tester.run_middle(report)
    assert all(s["passed"] for s in report), report
    aspects = {s["aspect"] for s in report}
    assert {"delete:missing_idempotent", "list_all:multi_key", "overwrite:shrink"} <= aspects


async def test_run_middle_dict_self_consistent() -> None:
    # 正=対象=辞書ストアなら run_middle も全観点一致（ツールの健全性）。
    tester = FileStoreTester(DictFileStore(), DictFileStore())
    report: list = []
    await tester.run_middle(report)
    assert all(s["passed"] for s in report)


# ── run_heavy（規模・境界の挙動契約・差分検証・M065） ──


async def test_run_heavy_local_file_store_matches_oracle(tmp_path) -> None:
    # 辞書ストアを正に LocalFileStore の大容量/分割 read/多キー/連続 overwrite を差分検証。
    tester = FileStoreTester(DictFileStore(), LocalFileStore(tmp_path))
    report: list = []
    await tester.run_heavy(report)
    assert all(s["passed"] for s in report), report
    aspects = {s["aspect"] for s in report}
    assert {"heavy:read_large_full", "heavy:read_segments", "heavy:read_after_regrow"} <= aspects


async def test_run_heavy_dict_self_consistent() -> None:
    # 正=対象=辞書ストアなら run_heavy も全観点一致（ツールの健全性）。
    tester = FileStoreTester(DictFileStore(), DictFileStore())
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

    broken = LocalFileStore(tmp_path)
    inner_open_reader = broken.open_reader

    async def open_reader(filename):  # noqa: ANN001  実 reader の中身を 64B 截断で包む
        r = await inner_open_reader(filename)
        async with r:
            data = await r.read(-1)
        return _TruncReader(data)

    broken.open_reader = open_reader
    tester = FileStoreTester(DictFileStore(), broken)
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

    broken = LocalFileStore(tmp_path)

    async def open_writer(filename):
        return _CommitOnExitWriter(broken, filename)

    broken.open_writer = open_writer
    with pytest.raises(AssertionError, match="all-or-nothing"):
        await assert_writer_aborts_on_error(broken)


# ── fail-loud 契約（fault-injection で下層障害の握り潰しを横断検知・M065 step2） ──


@pytest.mark.parametrize(
    "make_store",
    [
        pytest.param(lambda inner: inner, id="base_duality"),
        pytest.param(lambda inner: SafeKeyValueStore(inner), id="safe"),
        pytest.param(lambda inner: KeyValueFileStore(inner), id="kv_file_store"),
        pytest.param(lambda inner: DownloadCache(inner), id="download_cache"),
    ],
)
async def test_fail_loud_propagation_contract(make_store) -> None:
    # 下層（FaultInjectingKeyValueStore）の InjectedFault を握り潰さず伝播すること。基底の
    # get duality（NotFoundError 以外を default に化けさせない）も identity で同時に検証する。
    await assert_fail_loud_propagation(make_store)


async def test_fail_loud_contract_catches_swallowing_wrapper() -> None:
    # 契約の牙: exists を握り潰す（M055 と同型の）壊れた wrapper は契約で落ちる。
    class _SwallowExists(KeyValueStoreBase):
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
    ("kind", "base_name"), [("file", "FileStoreBase"), ("kv", "KeyValueStoreBase")]
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


def test_concrete_store_signatures_tolerate_return_narrowing() -> None:
    # ツール自体の健全性: concrete 実装の署名検証は **戻り注釈の narrowing を許容**しつつ
    # **パラメータ drift は捕える**。KeyValueStoreBase は戻り注釈まで一致＝当然 OK。
    assert concrete_store_signature_errors(KeyValueStoreBase, AsyncKeyValueStore) == []
    assert_concrete_store_signatures(KeyValueStoreBase, AsyncKeyValueStore)

    from collections.abc import AsyncIterable, AsyncIterator

    from manystore.protocols import FileInfo

    class _NarrowedReturn(
        KeyValueStoreBase
    ):  # 戻り注釈だけ narrowing（AsyncIterable→AsyncIterator）
        async def iter_all(  # type: ignore[override]
            self, limit: int | None = None, prefix: str = ""
        ) -> AsyncIterator[FileInfo]:
            yield FileInfo(filename="x", size=0)

    # 全 backend と同じ narrowing は許容＝違反ゼロ（strict base parity ならここを誤検出する）。
    assert concrete_store_signature_errors(_NarrowedReturn, AsyncKeyValueStore) == []

    class _ParamDrift(KeyValueStoreBase):  # put の if_match キーワードを落とす（実害ある drift）
        async def put(self, key: str, value: bytes) -> FileInfo:  # type: ignore[override]
            return FileInfo(filename=key, size=len(value))

    errors = concrete_store_signature_errors(_ParamDrift, AsyncKeyValueStore)
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
    # 実ストア（DictKeyValueStore）の create-only CAS は「一方成功・保存値=勝者」を満たす＝通る。
    # 後発を stagger で遅らせるので先行（b"A"）が勝つ＝どちらが優先されたかを内容で識別できる。
    winner = await assert_put_if_absent_concurrency_safe(DictKeyValueStore(), size=256)
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
    store = DictKeyValueStore()
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
    store = LocalKeyValueStore(tmp_path)
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
    store = DictKeyValueStore()
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
