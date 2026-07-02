"""manystore のテスト。

テストは `manystore` パッケージと同階層の `tests/` に置く（パッケージ dir はソースのみ＝
wheel にテストが入らない）。`pytest tests/`（または `make test`）で回す。
"""

import asyncio
import io
import os
from pathlib import Path

import pytest
from fakes import FakeNatsObs as _FakeNatsObs
from fakes import FakeS3 as _FakeS3
from fakes import patch_nats_obs as _patch_obs

from manystore import (
    DEFAULT_CACHE_DIR,
    ArrayKeyValueStore,
    AsyncToSyncKeyValueStore,
    ConnectPolicy,
    DictFileStore,
    DictKeyValueStore,
    DownloadCache,
    FileInfo,
    HttpFileStore,
    HttpKeyValueStore,
    IntegrityError,
    KeyValueFileStore,
    KeyValueFromFileStore,
    LocalFileStore,
    LocalKeyValueStore,
    NatsFileStore,
    NatsObjectKeyValueStore,
    NotFoundError,
    S3FileStore,
    S3KeyValueStore,
    SafeFileStore,
    SafeKeyValueStore,
    UnsafePathError,
    Verify,
    connect_key_value_store,
    connecting,
    create_safe_array_store,
    create_safe_file_store,
    create_safe_key_value_store,
    create_unsafe_file_store,
    create_unsafe_key_value_store,
    open_async_array_store,
    open_async_file_store,
    open_async_key_value_store,
    validate_safe_path,
)


def test_async_to_sync_kvs_roundtrip(tmp_path: Path) -> None:
    # 非同期 KeyValueStore を同期ブリッジで被せ、ループ無しの同期コードから put/get できる。
    with AsyncToSyncKeyValueStore(LocalKeyValueStore(tmp_path)) as store:
        assert store.exists("a.txt") is False
        store.put("a.txt", b"hello")
        assert store.exists("a.txt") is True
        assert store.get("a.txt") == b"hello"
        assert store.get("missing.txt") is None
        store.put("b.txt", b"x")
        # iter は async ジェネレータを同期イテレータとして流す（名前降順）。
        assert [i["filename"] for i in store.iter_all()] == ["b.txt", "a.txt"]
        assert [i["filename"] for i in store.list_all(limit=1)] == ["b.txt"]
        store.delete("a.txt")
        assert store.exists("a.txt") is False


@pytest.mark.parametrize("good", ["a.txt", "dir/b.txt", "a/b/c.bin"])
def test_validate_safe_path_allows_relative(good: str) -> None:
    assert validate_safe_path(good) == good


@pytest.mark.parametrize(
    "bad",
    ["", "/etc/passwd", "../secret", "a/../../b", "a\\b", "x\x00y"],
)
def test_validate_safe_path_rejects_unsafe(bad: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_safe_path(bad)


async def test_safe_kvs_validates_before_delegating(tmp_path: Path) -> None:
    safe = SafeKeyValueStore(LocalKeyValueStore(tmp_path))
    # 正常キー（サブディレクトリ付き）は通り、委譲先に書かれる。
    await safe.put("ok/a.txt", b"hi")
    assert await safe.get("ok/a.txt") == b"hi"
    # 不正キーは委譲前に弾く。
    with pytest.raises(UnsafePathError):
        await safe.put("../evil", b"x")
    with pytest.raises(UnsafePathError):
        await safe.get("/abs")


async def test_local_kvs_iter_and_list(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    for name in ("a", "b", "c"):
        await store.put(name, name.encode())
    # iter は全件を名前降順で yield する。
    names = [info["filename"] async for info in store.iter_all()]
    assert names == ["c", "b", "a"]
    # list は iter の先頭 limit 件。
    assert [i["filename"] for i in await store.list_all(limit=2)] == ["c", "b"]


async def test_local_file_store_open_write_read(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)

    # 書き込みモードは親ディレクトリを作って open できる。
    async with await store.open_writer("d/f.bin") as f:
        await f.write(b"hello")
    # 読み込みは open→read→（context manager で close）。
    async with await store.open_reader("d/f.bin") as f:
        assert await f.read() == b"hello"


async def test_local_kvs_put_creates_parent_dirs(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)
    # '/' を含むキーは親ディレクトリを作って格納できる（s3/nats のフラットキー規約に整合）。
    await store.put("a/b/c.bin", b"data")
    assert await store.get("a/b/c.bin") == b"data"


async def test_local_kvs_delete_removes_file_keeps_dirs(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    await store.put("a/b.txt", b"x")
    assert await store.exists("a/b.txt")
    await store.delete("a/b.txt")
    assert not await store.exists("a/b.txt")
    # ファイルだけ消す。親ディレクトリは残す。
    assert (tmp_path / "a").is_dir()
    # 無いキーの delete は無視（例外を投げない）。
    await store.delete("missing")


async def test_local_kvs_vacuum_removes_empty_dirs(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    await store.put("a/b/c.txt", b"x")
    await store.put("keep/d.txt", b"y")
    await store.delete("a/b/c.txt")  # a/b は空になるが delete は残す
    assert (tmp_path / "a" / "b").is_dir()
    await store.vacuum()
    # ネストした空ディレクトリ（a, a/b）は畳まれ、中身のある keep は残る。
    assert not (tmp_path / "a").exists()
    assert (tmp_path / "keep").is_dir()


async def test_local_kvs_cp_and_mv(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    await store.put("a.txt", b"hi")
    # cp は src を残して dst へ複製（dst のサブディレクトリも作る）。
    await store.cp("a.txt", "dir/b.txt")
    assert await store.get("a.txt") == b"hi"
    assert await store.get("dir/b.txt") == b"hi"
    # mv は src を消して dst へ（原子的 rename）。
    await store.mv("a.txt", "moved.txt")
    assert not await store.exists("a.txt")
    assert await store.get("moved.txt") == b"hi"
    # 無い src はエラー。
    with pytest.raises(NotFoundError):
        await store.cp("missing", "x")
    with pytest.raises(NotFoundError):
        await store.mv("missing", "x")


async def test_local_kvs_put_is_atomic(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    await store.put("k", b"v1")
    await store.put("k", b"v2")  # 原子的に差し替え
    assert await store.get("k") == b"v2"
    # 一時ファイルの残骸が無い（最終ファイルだけ）。
    assert [p.name for p in tmp_path.iterdir()] == ["k"]


async def test_put_returns_common_fileinfo(tmp_path: Path) -> None:
    # put は全 backend 共通レスポンス FileInfo（filename/size）を返す（protocols.py の契約）。
    # native KVS（dict）と FileStoreBase 導出（local）の両系統で同じ形を返す。
    d = await DictKeyValueStore().put("a.bin", b"hello")
    assert (d["filename"], d["size"]) == ("a.bin", 5)
    loc = await LocalKeyValueStore(tmp_path).put("k", b"xyz")
    assert (loc["filename"], loc["size"]) == ("k", 3)
    # array ルータは外向きキー（mount/subkey）で prefix し直して返す（subkey ではない）。
    arr = ArrayKeyValueStore()
    await arr.mount("docs", DictKeyValueStore())
    a = await arr.put("docs/a.txt", b"AB")
    assert (a["filename"], a["size"]) == ("docs/a.txt", 2)


async def test_local_file_store_write_is_atomic_on_error(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)

    async with await store.open_writer("k") as f:
        await f.write(b"old")
    # 書き込み中に例外 → 確定せず、既存値（old）が保たれる。
    with pytest.raises(RuntimeError):
        async with await store.open_writer("k") as f:
            await f.write(b"new-partial")
            raise RuntimeError("boom")
    async with await store.open_reader("k") as f:
        assert await f.read() == b"old"
    # 一時ファイルの残骸が無い。
    assert [p.name for p in tmp_path.iterdir()] == ["k"]


async def test_local_kvs_iter_is_recursive(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    await store.put("top.txt", b"1")
    await store.put("a/b/c.bin", b"2")
    # iter はサブディレクトリ配下のキーも相対 posix パスで列挙する。
    names = [info["filename"] async for info in store.iter_all()]
    assert names == ["top.txt", "a/b/c.bin"]  # 名前降順


async def test_key_value_file_store_open_over_kvs(tmp_path: Path) -> None:
    # KeyValueStore を FileStore として被せる（s3/nats も同型で FileStore 化できる）。
    fs = KeyValueFileStore(LocalKeyValueStore(tmp_path))

    async with await fs.open_writer("k/v.bin") as f:
        await f.write(b"abc")
        await f.write(b"de")  # close 時にまとめて put
    async with await fs.open_reader("k/v.bin") as f:
        assert await f.read() == b"abcde"
    # 無いキーの読み取りは NotFoundError。
    with pytest.raises(NotFoundError):
        await fs.open_reader("missing")


async def test_kvs_get_default_and_get_or_raise(tmp_path: Path) -> None:
    # get は欠損時にデフォルト値（既定 None）を返し、get_or_raise は NotFoundError を上げる。
    store = LocalKeyValueStore(tmp_path)

    # 欠損キー
    assert await store.get("missing") is None  # 既定デフォルト
    assert await store.get("missing", b"fallback") == b"fallback"  # 明示デフォルト
    with pytest.raises(NotFoundError):
        await store.get_or_raise("missing")
    # 存在キーは default を無視して実値を返す（get / get_or_raise とも）。
    await store.put("k", b"v")
    assert await store.get("k", b"fallback") == b"v"
    assert await store.get_or_raise("k") == b"v"


async def test_local_file_store_is_full_kvs(tmp_path: Path) -> None:
    # FileStore = KeyValueStore + IO。LocalFileStore は IO を持ちつつ KVS としても完全に働く。
    fs = LocalFileStore(tmp_path)

    # KVS 面: put / get(default) / get_or_raise / iter
    await fs.put("a/b.bin", b"hello")
    assert await fs.get("a/b.bin") == b"hello"
    assert await fs.get("missing") is None
    assert await fs.get("missing", b"def") == b"def"
    with pytest.raises(NotFoundError):
        await fs.get_or_raise("missing")
    assert [i["filename"] async for i in fs.iter_all()] == ["a/b.bin"]
    # IO 面: open_reader でも同じ真実が読める
    async with await fs.open_reader("a/b.bin") as r:
        assert await r.read() == b"hello"


async def test_key_value_file_store_is_full_file_store(tmp_path: Path) -> None:
    # KVS→FileStore は IO の埋め合わせ＝KVS 面は委譲しつつ open_reader/open_writer を合成。
    fs = KeyValueFileStore(LocalKeyValueStore(tmp_path))

    # IO 面（合成）
    async with await fs.open_writer("k.bin") as w:
        await w.write(b"xyz")
    async with await fs.open_reader("k.bin") as r:
        assert await r.read() == b"xyz"
    # KVS 面（下層へ委譲）も使える＝完全な FileStore
    assert await fs.get_or_raise("k.bin") == b"xyz"
    assert await fs.get("missing", b"d") == b"d"
    assert [i["filename"] async for i in fs.iter_all()] == ["k.bin"]
    # 欠損キーの open_reader は NotFoundError（get_or_raise 経由）
    with pytest.raises(NotFoundError):
        await fs.open_reader("missing")


async def test_key_value_from_file_store_derives_kvs(tmp_path: Path) -> None:
    # FileStore を KVS として被せる逆向きアダプタ（IO を落とすだけ・残りは下層へ委譲）。
    kv = KeyValueFromFileStore(LocalFileStore(tmp_path))

    await kv.put("a/b.bin", b"hello")  # 親ディレクトリは下層 writer が作る
    assert await kv.get("a/b.bin") == b"hello"
    assert await kv.get("missing") is None  # 欠損キーは None（KVS 規約）
    assert await kv.exists("a/b.bin") is True
    names = [info["filename"] async for info in kv.iter_all()]
    assert names == ["a/b.bin"]
    await kv.cp("a/b.bin", "c.bin")
    assert await kv.get("c.bin") == b"hello"
    await kv.mv("c.bin", "d.bin")
    assert await kv.exists("c.bin") is False
    assert await kv.get("d.bin") == b"hello"
    await kv.delete("a/b.bin")
    assert await kv.exists("a/b.bin") is False


async def test_local_kvs_is_thin_view_over_file_store(tmp_path: Path) -> None:
    # LocalKeyValueStore は LocalFileStore を被せた薄い KVS ビュー（実装は FileStore 側に集約）。
    store = LocalKeyValueStore(tmp_path)
    assert isinstance(store, KeyValueFromFileStore)

    # KVS 経由の put は、同じ dir の FileStore から open_reader でも読める（同一の真実）。
    await store.put("shared.bin", b"xyz")
    fs = LocalFileStore(tmp_path)
    async with await fs.open_reader("shared.bin") as f:
        assert await f.read() == b"xyz"


async def test_safe_file_store_validates_filename(tmp_path: Path) -> None:
    safe = SafeFileStore(LocalFileStore(tmp_path))

    async with await safe.open_writer("ok/a.bin") as f:
        await f.write(b"x")
    async with await safe.open_reader("ok/a.bin") as f:
        assert await f.read() == b"x"
    with pytest.raises(UnsafePathError):
        await safe.open_reader("../evil")


async def test_local_kvs_path_fixed_at_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 相対パスで初期化しても、初期化時の cwd を基準に絶対パスへ固定される。
    monkeypatch.chdir(tmp_path)
    (tmp_path / "store").mkdir()
    store = LocalKeyValueStore(Path("store"))
    await store.put("k", b"v")
    # 実行中に cd しても、初期化時に固定したパスを参照する。
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert await store.get("k") == b"v"
    assert (tmp_path / "store" / "k").read_bytes() == b"v"


# ── S3 streaming file store（fake S3 client で分割ロジックを検証） ──


async def test_s3_file_store_streams_multipart_write_and_read() -> None:
    fake = _FakeS3()
    store = S3FileStore("bucket", part_size=4)  # 小さなパートで分割を起こす
    store._session = lambda: fake  # 接続を fake に差し替え

    # 11 バイトを part_size=4 で書く → パート分割（4,4,3）して multipart upload。
    async with await store.open_writer("k") as f:
        await f.write(b"hello world")
    assert fake.objects["k"] == b"hello world"
    assert len(fake._uploads) == 0  # complete 済み

    # ストリーム read（全体／chunk）。
    async with await store.open_reader("k") as f:
        assert await f.read() == b"hello world"
    async with await store.open_reader("k") as f:
        assert await f.read(5) == b"hello"
        assert await f.read() == b" world"

    # 空書き込みは空オブジェクトを put（multipart 0 パート不可）。
    async with await store.open_writer("empty") as f:
        pass
    assert fake.objects["empty"] == b""


async def test_s3_file_store_is_full_kvs() -> None:
    # S3FileStore = S3KeyValueStore + streaming IO。KVS 面（whole get/put）も持つ完全 FileStore。
    fake = _FakeS3()
    store = S3FileStore("bucket")
    store._session = lambda: fake

    await store.put("kv", b"data")  # 継承 put＝put_object（whole）
    assert fake.objects["kv"] == b"data"
    assert await store.get("kv") == b"data"  # 継承 get（whole get_object）
    assert await store.get_or_raise("kv") == b"data"
    assert await store.get("missing") is None  # 欠損は default
    assert await store.get("missing", b"d") == b"d"
    with pytest.raises(NotFoundError):
        await store.get_or_raise("missing")
    assert await store.exists("kv") is True  # head_object 200
    assert await store.exists("missing") is False  # head_object 404


async def test_s3_exists_propagates_non_404() -> None:
    # fail-loud: 404/NoSuchKey/NotFound 以外（認証・5xx・接続断）は握り潰さず伝播する。
    from botocore.exceptions import ClientError

    fake = _FakeS3()
    fake.head_error_code = "500"
    store = S3KeyValueStore("bucket")
    store._session = lambda: fake

    with pytest.raises(ClientError):
        await store.exists("k")


# ── NATS backend（fake object store は tests/fakes.py・実 nats-py の API 形に合わせる） ──


async def test_nats_file_store_buffered_read_write() -> None:
    store = NatsFileStore("nats://x", "bucket")
    fake = _FakeNatsObs()
    _patch_obs(store, fake)

    # write はバッファして close で put。
    async with await store.open_writer("k") as f:
        await f.write(b"hello")
        await f.write(b" world")
    assert fake.objects["k"] == b"hello world"

    # read は全体取得してバッファから返す（全体／chunk）。
    async with await store.open_reader("k") as f:
        assert await f.read() == b"hello world"
    async with await store.open_reader("k") as f:
        assert await f.read(5) == b"hello"
        assert await f.read() == b" world"

    # 無いキーは NotFoundError。
    with pytest.raises(NotFoundError):
        await store.open_reader("missing")


async def test_nats_file_store_is_full_kvs() -> None:
    # NatsFileStore = NatsObjectKeyValueStore + buffer 合成 IO。KVS 面も使える完全な FileStore。
    store = NatsFileStore("nats://x", "bucket")
    fake = _FakeNatsObs()
    _patch_obs(store, fake)

    await store.put("kv", b"data")  # 継承 put（whole）
    assert fake.objects["kv"] == b"data"
    assert await store.get("kv") == b"data"  # 継承 get（whole）
    assert await store.get_or_raise("kv") == b"data"
    assert await store.get("missing") is None
    with pytest.raises(NotFoundError):
        await store.get_or_raise("missing")
    assert await store.exists("kv") is True
    assert [i["filename"] async for i in store.iter_all()] == ["kv"]


async def test_nats_kvs_exists_uses_get_info() -> None:
    # exists は get_info を使う（ObjectStore に info は無い）。
    store = NatsObjectKeyValueStore("nats://x", "bucket")
    fake = _FakeNatsObs()
    _patch_obs(store, fake)

    assert await store.exists("k") is False
    await store.put("k", b"v")
    assert await store.exists("k") is True


async def test_nats_iter_all_empty_is_not_error() -> None:
    # 空ストアは list() が NotFoundError を上げるが iter_all は [] 扱い（エラーにしない）。
    store = NatsObjectKeyValueStore("nats://x", "bucket")
    _patch_obs(store, _FakeNatsObs())

    assert [i async for i in store.iter_all()] == []


async def test_nats_iter_all_propagates_real_error() -> None:
    # fail-loud: 空（NotFoundError）以外の障害（接続断など）は握り潰さず伝播する。
    store = NatsObjectKeyValueStore("nats://x", "bucket")
    fake = _FakeNatsObs()

    async def boom(ignore_deletes: bool = False) -> list:
        raise RuntimeError("connection lost")

    fake.list = boom
    _patch_obs(store, fake)

    with pytest.raises(RuntimeError):
        [i async for i in store.iter_all()]


# ── connection lifecycle（接続前 factory 包み → async with で接続） ──


async def test_connect_key_value_store_local_roundtrip(tmp_path: Path) -> None:
    # init では接続せず、async with で connect してから使う。
    async with connect_key_value_store("local", local_dir=tmp_path) as store:
        await store.put("k", b"v")
        assert await store.get("k") == b"v"


async def test_local_kvs_connect_aclose_lifecycle(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    await store.connect()  # ローカルは体裁上のステップ（dir を用意するだけ）
    await store.put("k", b"v")
    await store.aclose()


class _BadConnectStore:
    """connect が必ず失敗するストア（verify の挙動確認用）。"""

    def __init__(self) -> None:
        self.aclosed = False

    async def connect(self) -> None:
        raise ConnectionError("boom")

    async def aclose(self) -> None:
        self.aclosed = True


def test_connect_policy_presets() -> None:
    assert ConnectPolicy.default() == ConnectPolicy()  # 既定と一致
    ff = ConnectPolicy.fail_fast()
    assert ff.max_retry == 0 and ff.timeout == 5.0 and ff.deadline == 5.0
    fv = ConnectPolicy.forever()
    # 無期限に粘る: deadline 無し・無制限リトライ・per-attempt timeout は有限。
    assert fv.max_retry == float("inf") and fv.deadline is None and fv.timeout == 10.0


# 既定 policy は max_retry=inf（無制限）で deadline まで粘るため、verify の検証は
# リトライ無し（max_retry=0）で即決させる。
_NO_RETRY = ConnectPolicy(max_retry=0)


async def test_connecting_verify_true_raises_and_acloses() -> None:
    bad = _BadConnectStore()

    with pytest.raises(ConnectionError):
        async with connecting(lambda: bad, policy=_NO_RETRY):  # verify=True 既定
            pass

    assert bad.aclosed is True  # 失敗時も後始末される


async def test_connecting_verify_false_ignores_failure() -> None:
    bad = _BadConnectStore()

    # verify=False は初回接続失敗を無視して中へ入れる。
    async with connecting(lambda: bad, verify=False, policy=_NO_RETRY) as store:
        assert store is bad


# ── 接続リトライ方針（ConnectPolicy） ──


class _FlakyConnectStore:
    """connect が最初 `fail_times` 回だけ失敗するストア。"""

    def __init__(self, fail_times: int) -> None:
        self._left = fail_times
        self.attempts = 0
        self.aclosed = False

    async def connect(self) -> None:
        self.attempts += 1
        if self._left > 0:
            self._left -= 1
            raise ConnectionError("flaky")

    async def aclose(self) -> None:
        self.aclosed = True


async def test_retry_until_success() -> None:
    flaky = _FlakyConnectStore(fail_times=2)

    async with connecting(lambda: flaky, policy=ConnectPolicy(max_retry=2, delay=0)) as s:
        assert s is flaky

    assert flaky.attempts == 3  # 2 回失敗 + 1 回成功（max_retry=2 → 総 3 試行）


async def test_retry_exhausted_raises() -> None:
    flaky = _FlakyConnectStore(fail_times=5)

    with pytest.raises(ConnectionError):
        async with connecting(lambda: flaky, policy=ConnectPolicy(max_retry=1, delay=0)):
            pass

    assert flaky.attempts == 2  # max_retry=1 → 総 2 試行で打ち切り
    assert flaky.aclosed is True


async def test_retry_timeout_per_attempt() -> None:
    class _HangStore:
        async def connect(self) -> None:
            await asyncio.sleep(10)  # 1 回の connect がハング

        async def aclose(self) -> None:
            return None

    policy = ConnectPolicy(max_retry=0, timeout=0.01)

    with pytest.raises(TimeoutError):
        async with connecting(lambda: _HangStore(), policy=policy):
            pass


async def test_retry_deadline_bounds_hung_connect_without_timeout() -> None:
    class _HangStore:
        async def connect(self) -> None:
            await asyncio.sleep(10)  # ハングする connect

        async def aclose(self) -> None:
            return None

    # timeout=None でも deadline があれば 1 回のハングで無限待機しない。
    policy = ConnectPolicy(max_retry=0, timeout=None, deadline=0.02)

    with pytest.raises(TimeoutError):
        async with connecting(lambda: _HangStore(), policy=policy):
            pass


async def test_retry_deadline_stops_early() -> None:
    flaky = _FlakyConnectStore(fail_times=100)

    with pytest.raises(ConnectionError):
        async with connecting(
            lambda: flaky,
            policy=ConnectPolicy(max_retry=float("inf"), delay=0.02, deadline=0.05),
        ):
            pass

    assert flaky.attempts < 100  # deadline で attempts 到達前に打ち切り


async def test_retry_verify_false_ignores_after_exhaustion() -> None:
    flaky = _FlakyConnectStore(fail_times=100)

    # 粘っても駄目だが verify=False なので無視して中へ。
    async with connecting(
        lambda: flaky, verify=False, policy=ConnectPolicy(max_retry=1, delay=0)
    ) as s:
        assert s is flaky

    assert flaky.attempts == 2


# ── download cache（KeyValueStore → ローカルキャッシュ） ──


async def test_download_cache_fetches_caches_and_force(tmp_path: Path) -> None:
    upstream = LocalKeyValueStore(tmp_path / "remote")  # 上流ストア（リモート相当）
    cache = DownloadCache(upstream, cache_dir=tmp_path / "cache")

    await upstream.put("m/model.bin", b"weights")
    p = await cache.download("m/model.bin")
    assert p == tmp_path / "cache" / "m" / "model.bin"
    assert p.read_bytes() == b"weights"

    # 上流を変えてもキャッシュ済みなら再取得しない（存在ベース）。
    await upstream.put("m/model.bin", b"CHANGED")
    assert (await cache.download("m/model.bin")).read_bytes() == b"weights"
    # force=True で取り直す。
    assert (await cache.download("m/model.bin", force=True)).read_bytes() == b"CHANGED"

    # 上流に無ければ NotFoundError。
    with pytest.raises(NotFoundError):
        await cache.download("missing")


async def test_download_cache_rejects_unsafe_key(tmp_path: Path) -> None:
    cache = DownloadCache(LocalKeyValueStore(tmp_path / "r"), cache_dir=tmp_path / "c")

    with pytest.raises(UnsafePathError):
        await cache.download("../evil")  # キャッシュ外へ書かせない


async def test_head_sha256_metadata(tmp_path: Path) -> None:
    import hashlib

    value = b"weights-123"
    want = hashlib.sha256(value).hexdigest()

    # dict（native メタを並列 dict で持つ）は put 時に sha256 を埋め head で返す（M013）。
    d = DictKeyValueStore()
    await d.put("a.bin", value)
    assert (await d.head("a.bin")).get("sha256") == want
    # 更新で hash も追従。
    await d.put("a.bin", b"x")
    assert (await d.head("a.bin")).get("sha256") == hashlib.sha256(b"x").hexdigest()
    # 外部から直接 dict に挿入したキーはメタ無し＝None（best-effort）。
    shared: dict[str, bytes] = {}
    ext = DictKeyValueStore(shared)
    shared["raw"] = b"zzz"
    assert (await ext.head("raw")).get("sha256") is None

    # local は native メタを持たない＝非永続で head は sha256=None（size 検証は有効・M013 方針）。
    loc = LocalKeyValueStore(tmp_path / "r")
    await loc.put("k", value)
    assert (await loc.head("k")).get("sha256") is None


async def test_local_iter_all_skips_file_vanished_mid_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 走査〜stat の間に並行 delete で消えたファイル（ループ内 stat が FileNotFoundError）は、
    # 生の例外を漏らさず一覧から除く（M072）。タイミング依存を避け、消滅の瞬間を確定的に再現する
    # ＝対象ファイルの stat を「is_file の 1 回目は通し、ループ内 2 回目で欠損」に差し替える。
    store = LocalKeyValueStore(tmp_path)
    await store.put("gone.bin", b"x")
    await store.put("stay.bin", b"y")

    real_os_stat = os.stat
    seen = {"n": 0}

    def flaky_os_stat(path: object, *a: object, **k: object) -> object:
        if str(path).endswith("gone.bin"):
            seen["n"] += 1
            if seen["n"] >= 2:  # is_file()=1回目は通し、iter_all ループ内 stat=2回目で消えた扱い
                raise FileNotFoundError(str(path))
        return real_os_stat(path, *a, **k)

    monkeypatch.setattr(os, "stat", flaky_os_stat)

    names = [info["filename"] async for info in store.iter_all()]
    assert "stay.bin" in names  # 生存ファイルは列挙される
    assert "gone.bin" not in names  # 走査中に消えたファイルは除外（例外を漏らさない）


async def test_local_delete_idempotent_under_toctou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 並行 delete の TOCTOU（is_file()=True 直後に別 delete が消し unlink が欠損）を確定再現。
    # is_file を True 固定＋ファイルを先に消す＝旧 is_file()→unlink() は生 FNF を漏らし、
    # 修正版 unlink(missing_ok=True) は no-op（冪等）。多数反復に頼らず決定的に検査（M072）。
    store = LocalKeyValueStore(tmp_path)
    await store.put("k", b"x")
    os.unlink(tmp_path / "k")  # 並行 delete が先に消した状況を作る
    monkeypatch.setattr(Path, "is_file", lambda self: True)  # is_file=True（TOCTOU の窓を固定）
    await store.delete("k")  # 例外を出さない＝生 FileNotFoundError を漏らさない（冪等）


class _FakeMetaStore:
    """`get_or_raise`/`head` だけ持つストア。head の返すメタを自由に固定して検証分岐を突く。"""

    def __init__(self, data: bytes, info: FileInfo) -> None:
        self._data = data
        self._info = info

    async def get_or_raise(self, key: str) -> bytes:
        return self._data

    async def head(self, key: str) -> FileInfo:
        return self._info


async def test_download_verify_size(tmp_path: Path) -> None:
    data = b"weights-123"

    # size 一致＝既定（DEFAULT）で pass（hash は無いので best-effort スキップ）。
    ok = DownloadCache(
        _FakeMetaStore(data, FileInfo("m/x.bin", size=len(data))), cache_dir=tmp_path / "a"
    )
    assert (await ok.download("m/x.bin")).read_bytes() == data

    # size 不一致＝IntegrityError（head が嘘の size を返す）。
    bad = DownloadCache(
        _FakeMetaStore(data, FileInfo("m/x.bin", size=len(data) + 1)), cache_dir=tmp_path / "b"
    )
    with pytest.raises(IntegrityError):
        await bad.download("m/x.bin")
    # verify=NONE なら検証せず素通り（head も引かない）。
    assert (await bad.download("m/x.bin", verify=Verify.NONE)).read_bytes() == data


async def test_download_verify_hash(tmp_path: Path) -> None:
    import hashlib

    data = b"weights-123"
    good = hashlib.sha256(data).hexdigest()

    # sha256 一致＝DEFAULT（hash あれば照合）で pass。
    m = DownloadCache(
        _FakeMetaStore(data, FileInfo("m/x.bin", size=len(data), sha256=good)),
        cache_dir=tmp_path / "a",
    )
    assert (await m.download("m/x.bin")).read_bytes() == data

    # sha256 不一致＝IntegrityError。
    bad = DownloadCache(
        _FakeMetaStore(data, FileInfo("m/x.bin", size=len(data), sha256="dead")),
        cache_dir=tmp_path / "b",
    )
    with pytest.raises(IntegrityError):
        await bad.download("m/x.bin")

    # hash 無し: DEFAULT は best-effort で素通り／STRICT は必須ゆえ失敗／SIZE のみは hash 無視。
    nohash = DownloadCache(
        _FakeMetaStore(data, FileInfo("m/x.bin", size=len(data))), cache_dir=tmp_path / "c"
    )
    with pytest.raises(IntegrityError):
        await nohash.download("m/x.bin", verify=Verify.STRICT)
    assert (await nohash.download("m/x.bin", verify=Verify.SIZE)).read_bytes() == data
    assert (
        await nohash.download("m/x.bin", verify=Verify.DEFAULT, force=True)
    ).read_bytes() == data


def test_download_cache_default_dir_under_home(tmp_path: Path) -> None:
    cache = DownloadCache(LocalKeyValueStore(tmp_path))  # cache_dir 省略
    assert cache.cache_dir == DEFAULT_CACHE_DIR.resolve()
    assert str(cache.cache_dir).startswith(str(Path.home().resolve()))


def test_download_cache_dir_fixed_absolute_at_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # 相対 cache_dir でも init 時の cwd を基準に絶対パスへ固定する（cd 非依存）。
    cache = DownloadCache(LocalKeyValueStore(tmp_path / "r"), cache_dir="mycache")
    assert cache.cache_dir.is_absolute()
    assert cache.cache_dir == (tmp_path / "mycache").resolve()


# ── ArrayKeyValueStore（論理名で複数 backend を束ねる） ──


async def test_array_kvs_mount_and_route(tmp_path: Path) -> None:
    arr = ArrayKeyValueStore()

    await arr.mount("docs", LocalKeyValueStore(tmp_path / "docs"))  # 登録のみ（I/O なし）
    await arr.mount("imgs", LocalKeyValueStore(tmp_path / "imgs"))
    await arr.put("docs/a.txt", b"A")
    await arr.put("imgs/p/q.bin", b"B")  # サブディレクトリ込み
    assert await arr.get("docs/a.txt") == b"A"
    assert await arr.get("imgs/p/q.bin") == b"B"
    # 論理名そのものはディレクトリ扱いで存在する。
    assert await arr.exists("docs") is True
    assert await arr.exists("docs/a.txt") is True
    assert await arr.exists("nope") is False
    assert arr.mounts() == ["docs", "imgs"]
    # iter は論理名を prefix して全 backend を横断する。
    names = sorted([i["filename"] async for i in arr.iter_all()])
    assert names == ["docs/a.txt", "imgs/p/q.bin"]
    # 未知 mount / 形式不正はエラー。
    with pytest.raises(KeyError):
        await arr.get("unknown/x")
    with pytest.raises(KeyError):
        await arr.get("docs")  # subkey 無し
    # delete も振り分け。
    await arr.delete("docs/a.txt")
    assert await arr.exists("docs/a.txt") is False


async def test_array_kvs_cp_mv_across_mounts(tmp_path: Path) -> None:
    arr = ArrayKeyValueStore()

    await arr.mount("a", LocalKeyValueStore(tmp_path / "a"))
    await arr.mount("b", LocalKeyValueStore(tmp_path / "b"))
    await arr.put("a/x", b"data")
    await arr.cp("a/x", "b/y")  # mount 跨ぎ copy
    assert await arr.get("b/y") == b"data"
    assert await arr.get("a/x") == b"data"  # src は残る
    await arr.mv("a/x", "b/z")  # mount 跨ぎ move
    assert await arr.get("a/x") is None
    assert await arr.get("b/z") == b"data"


async def test_array_kvs_cp_native_within_mount_else_get_put(tmp_path: Path) -> None:
    # M064: 同一ストアオブジェクト（=同一 mount）なら native cp/mv へ委譲、跨ぎは get→put。
    native_calls: list[tuple[str, str, str]] = []

    class _NativeSpy(LocalKeyValueStore):
        async def cp(self, src: str, dst: str) -> None:
            native_calls.append(("cp", src, dst))
            await super().cp(src, dst)

        async def mv(self, src: str, dst: str) -> None:
            native_calls.append(("mv", src, dst))
            await super().mv(src, dst)

    arr = ArrayKeyValueStore()
    await arr.mount("m", _NativeSpy(tmp_path / "m"))  # 同一 mount は同一ストアオブジェクト
    await arr.mount("other", LocalKeyValueStore(tmp_path / "other"))
    await arr.put("m/x", b"data")

    await arr.cp("m/x", "m/y")  # 同一 mount → native（subkey で委譲）
    assert native_calls == [("cp", "x", "y")]  # 論理名を剥がした subkey で native cp
    assert await arr.get("m/y") == b"data"

    await arr.cp("m/x", "other/z")  # 跨ぎ → get→put（native は呼ばれない）
    assert native_calls == [("cp", "x", "y")]  # 増えていない
    assert await arr.get("other/z") == b"data"


class _LifecycleRecorder:
    """connect/aclose の呼び出しを記録する最小スタブ（mount の責務分離を検証する用）。"""

    def __init__(self) -> None:
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def aclose(self) -> None:
        self.closed = True


async def test_array_mount_is_registration_only() -> None:
    # mount は登録のみ＝I/O なし（connect しない）。接続は合成ストアの connect() が一括で担う。
    arr = ArrayKeyValueStore()
    rec = _LifecycleRecorder()

    await arr.mount("x", rec)  # 登録のみ（非同期 IF だが現状 I/O なし）
    assert rec.connected is False  # mount は connect しない（二重責務を持たない）
    assert arr.mounts() == ["x"]
    await arr.connect()  # 接続は合成ストア側でまとめて行う
    assert rec.connected is True


async def test_open_async_array_store_connects_and_closes_mounts() -> None:
    # 顔の入口は mount 群を登録して CM 突入で connect・終了で aclose する（ライフサイクル一括）。
    rec = _LifecycleRecorder()

    async with open_async_array_store({"x": rec}) as _arr:
        assert rec.connected is True  # 突入時に connect 済み（全 mount を一括接続）
        assert rec.closed is False
    assert rec.closed is True  # 退出時に aclose


# ── HTTP backend（read-only。fake httpx client で get/exists/read を検証） ──


class _FakeHttpResp:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpClient:
    """Http*Store を駆動する最小 fake（async context manager 兼 httpx.AsyncClient 代替）。"""

    def __init__(self, objects: dict[str, bytes], base_url: str) -> None:
        self.objects = objects
        self._base = base_url.rstrip("/") + "/"

    async def __aenter__(self) -> _FakeHttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _key(self, url: str) -> str:
        return url[len(self._base) :] if url.startswith(self._base) else url

    async def get(self, url: str) -> _FakeHttpResp:
        key = self._key(url)
        if key in self.objects:
            return _FakeHttpResp(200, self.objects[key])
        return _FakeHttpResp(404)

    async def head(self, url: str) -> _FakeHttpResp:
        return _FakeHttpResp(200 if self._key(url) in self.objects else 404)


_HTTP_BASE = "http://example.test"


async def test_http_kvs_get_and_exists() -> None:
    objects = {"a.txt": b"hello", "dir/b.bin": b"\x00\x01"}
    store = HttpKeyValueStore(base_url=_HTTP_BASE)
    store._client = lambda: _FakeHttpClient(objects, _HTTP_BASE)  # 接続を fake に差し替え

    assert await store.get("a.txt") == b"hello"
    assert await store.get("dir/b.bin") == b"\x00\x01"  # 多段キーも base_url 相対で取れる
    assert await store.get("missing") is None  # 404 → None
    assert await store.exists("a.txt") is True
    assert await store.exists("missing") is False


async def test_http_file_store_is_full_read_only_kvs() -> None:
    # HttpFileStore = HttpKeyValueStore + read IO。KVS 面（read-only）も持つ完全な FileStore。
    objects = {"a.txt": b"hello"}
    store = HttpFileStore(base_url=_HTTP_BASE)
    store._client = lambda: _FakeHttpClient(objects, _HTTP_BASE)

    # read IO（whole get の buffer 合成）
    async with await store.open_reader("a.txt") as f:
        assert await f.read() == b"hello"
    with pytest.raises(NotFoundError):
        await store.open_reader("missing")
    # KVS 面（継承）も使える
    assert await store.get("a.txt") == b"hello"
    assert await store.get("missing", b"d") == b"d"
    assert await store.exists("a.txt") is True
    # write 系は read-only ＝ io.UnsupportedOperation（型上は存在するが実行時に拒否）
    for call in (
        store.put("x", b"v"),
        store.delete("x"),
        store.open_writer("x"),
    ):
        with pytest.raises(io.UnsupportedOperation):
            await call


async def test_http_kvs_is_read_only() -> None:
    store = HttpKeyValueStore(base_url=_HTTP_BASE)

    with pytest.raises(io.UnsupportedOperation):
        await store.put("k", b"v")
    with pytest.raises(io.UnsupportedOperation):
        await store.delete("k")
    with pytest.raises(io.UnsupportedOperation):
        await store.cp("a", "b")
    with pytest.raises(io.UnsupportedOperation):
        await store.mv("a", "b")
    with pytest.raises(io.UnsupportedOperation):
        await store.list_all()
    with pytest.raises(io.UnsupportedOperation):
        async for _ in store.iter_all():
            pass


async def test_http_file_store_read() -> None:
    objects = {"a.txt": b"hello"}
    fs = HttpFileStore(base_url=_HTTP_BASE)
    fs._client = lambda: _FakeHttpClient(objects, _HTTP_BASE)

    async with await fs.open_reader("a.txt") as f:
        assert await f.read() == b"hello"
    with pytest.raises(NotFoundError):  # 404 → NotFoundError
        await fs.open_reader("missing")
    with pytest.raises(io.UnsupportedOperation):  # write は非対応
        await fs.open_writer("a.txt")


def test_create_unsafe_key_value_store_http_wiring() -> None:
    # ファクトリ経由で backend="http" → HttpKeyValueStore が組み立つ（base_url/headers を渡す）。
    store = create_unsafe_key_value_store(
        "http", http_base_url=_HTTP_BASE, http_headers={"Authorization": "Bearer t"}
    )
    assert isinstance(store, HttpKeyValueStore)
    assert store._base_url == _HTTP_BASE
    assert store._headers == {"Authorization": "Bearer t"}


# ── prefix を iter_all/list_all の引数に畳む（capability iter_prefix は廃止） ──


class _IterAllRecorder:
    """`iter_all(limit, prefix)` を持つ最小ストア。受け取った prefix を記録する。"""

    def __init__(self, keys: dict[str, int]) -> None:
        self._keys = dict(keys)  # filename -> size
        self.prefix_calls: list[str] = []

    async def connect(self) -> None: ...

    async def aclose(self) -> None: ...

    async def iter_all(self, limit: int | None = None, prefix: str = ""):
        self.prefix_calls.append(prefix)
        count = 0
        for k, size in sorted(self._keys.items(), reverse=True):  # 降順
            if prefix and not k.startswith(prefix):
                continue
            if limit is not None and count >= limit:
                return
            yield {"filename": k, "size": size}
            count += 1


async def test_dict_store_prefix_via_scan_filter() -> None:
    # サーバ側 prefix を持たない backend（Dict）は iter_all(prefix=) を scan+filter で支える。
    store = DictKeyValueStore()

    await store.put("a/1", b"x")
    await store.put("a/2", b"yy")
    await store.put("b/1", b"zzz")
    got = sorted([i["filename"] async for i in store.iter_all(prefix="a/")])
    assert got == ["a/1", "a/2"]  # b/1 は除外
    empty = sorted([i["filename"] async for i in store.iter_all()])
    assert empty == ["a/1", "a/2", "b/1"]  # 既定 prefix="" = 全件
    # limit は prefix 絞り込み後の件数上限。
    one = [i["filename"] async for i in store.iter_all(limit=1, prefix="a/")]
    assert one == ["a/2"]  # 降順先頭


async def test_safe_store_iter_all_validates_prefix_then_delegates() -> None:
    rec = _IterAllRecorder({"ok/1": 1, "ok/2": 2, "no/1": 3})
    safe = SafeKeyValueStore(rec)

    got = [i["filename"] async for i in safe.iter_all(prefix="ok/")]
    assert got == ["ok/2", "ok/1"]
    assert rec.prefix_calls == ["ok/"]  # 検証後に下層 iter_all(prefix=) へ委譲
    # 不正な prefix は委譲前に弾く（traversal）。
    with pytest.raises(UnsafePathError):
        async for _ in safe.iter_all(prefix="../etc"):
            pass
    # 空 prefix は「全件」＝検証を飛ばす（validate_safe_path は空を弾くため）。
    allk = [i["filename"] async for i in safe.iter_all()]
    assert allk == ["ok/2", "ok/1", "no/1"]


async def test_array_store_iter_all_prefix_routes_to_single_mount() -> None:
    m1 = _IterAllRecorder({"x/1": 1, "y/2": 2})
    m2 = _IterAllRecorder({"x/9": 9})
    arr = ArrayKeyValueStore()

    await arr.mount("m1", m1)
    await arr.mount("m2", m2)
    # `<mount>/<subprefix>` は単一 mount へ振り分け subprefix を委譲（他 mount は触らない）。
    got = [i["filename"] async for i in arr.iter_all(prefix="m1/x/")]
    assert got == ["m1/x/1"]  # m1 の x/* のみ・mount 名で再前置
    assert m1.prefix_calls == ["x/"]  # subprefix を下層 iter_all へ素通し
    assert m2.prefix_calls == []  # m2 は走査されない
    # `/` 無しの prefix は（部分）mount 名一致＝該当 mount を丸ごと列挙する。
    whole = sorted([i["filename"] async for i in arr.iter_all(prefix="m1")])
    assert whole == ["m1/x/1", "m1/y/2"]
    # 無い mount は空。
    assert [i async for i in arr.iter_all(prefix="zzz/a")] == []
    # prefix 無し＝全 mount 横断（mount 名降順）。
    everything = [i["filename"] async for i in arr.iter_all()]
    assert sorted(everything) == ["m1/x/1", "m1/y/2", "m2/x/9"]


async def test_s3_iter_all_filters_server_side() -> None:
    fake = _FakeS3()
    store = S3KeyValueStore("bucket")
    store._session = lambda: fake  # 接続を fake に差し替え

    await store.put("a/1", b"x")
    await store.put("a/2", b"yy")
    await store.put("b/1", b"zzz")
    # ネイティブ prefix 絞り（サーバ側 list_objects_v2(Prefix=…)）・降順は不変。
    got = [i["filename"] async for i in store.iter_all(prefix="a/")]
    assert got == ["a/2", "a/1"]
    # 既定 prefix="" は全件・降順。
    allk = [i["filename"] async for i in store.iter_all()]
    assert allk == ["b/1", "a/2", "a/1"]


# ── 安全な入口の最終形＝open_async_* / create_safe_* / create_unsafe_*（M032 / M011-①） ──


async def test_open_async_key_value_store_is_safe_and_connected(tmp_path: Path) -> None:
    # 顔: async with で Safe 包装＋接続済みの KVS を得る（生 backend を直接触らせない）。
    async with open_async_key_value_store("local", local_dir=tmp_path) as store:
        assert isinstance(store, SafeKeyValueStore)  # Safe 包装は必須
        await store.put("a/b.txt", b"hi")
        assert await store.get("a/b.txt") == b"hi"
        with pytest.raises(UnsafePathError):
            await store.put("../escape", b"x")  # パストラバーサルは弾く


async def test_open_async_file_store_is_safe_full_filestore(tmp_path: Path) -> None:
    # 顔: Safe 包装＋接続済みの完全な FileStore（= KVS + IO）。
    async with open_async_file_store("local", local_dir=tmp_path) as fs:
        assert isinstance(fs, SafeFileStore)
        # IO 面（filename 検証付き）
        async with await fs.open_writer("k/v.bin") as w:
            await w.write(b"data")
        async with await fs.open_reader("k/v.bin") as r:
            assert await r.read() == b"data"
        # KVS 面も使える（FileStore = KVS + IO）＋キー検証
        assert await fs.get("k/v.bin") == b"data"
        assert await fs.exists("k/v.bin") is True
        with pytest.raises(UnsafePathError):
            await fs.open_reader("../escape")
        with pytest.raises(UnsafePathError):
            await fs.delete("../escape")  # KVS 面も検証付き


async def test_open_async_uses_memory_backend_without_connect() -> None:
    # memory は接続不要・揮発。顔から開いても Safe 包装される。
    async with open_async_key_value_store("memory") as store:
        assert isinstance(store, SafeKeyValueStore)
        await store.put("k", b"v")
        assert await store.get("k") == b"v"


def test_create_unsafe_file_store_maps_backends(tmp_path: Path) -> None:
    # FileStore 版ファクトリ（生・未包装・未接続）。backend→FileStore のマッピング。
    assert isinstance(create_unsafe_file_store("memory"), DictFileStore)
    assert isinstance(create_unsafe_file_store("local", local_dir=tmp_path), LocalFileStore)
    assert isinstance(create_unsafe_file_store("http", http_base_url="http://x"), HttpFileStore)
    with pytest.raises(ValueError):
        create_unsafe_file_store("nope")
    with pytest.raises(ValueError):
        create_unsafe_file_store("local")  # local_dir 必須


async def test_create_safe_factories_wrap_without_connecting(tmp_path: Path) -> None:
    # create_safe_* は Safe 包装のみ（構築だけ・未接続）。接続せずともキー検証は効く。
    kv = create_safe_key_value_store("memory")
    assert isinstance(kv, SafeKeyValueStore)
    fs = create_safe_file_store("local", local_dir=tmp_path)
    assert isinstance(fs, SafeFileStore)

    # create_safe_array_store は async（mount が非同期 IF のため）＝構築のみ・未接続。
    arr = await create_safe_array_store({"docs": create_unsafe_key_value_store("memory")})
    assert isinstance(arr, SafeKeyValueStore)
    # 未接続でも memory backend は使える（接続不要）＋ Safe 検証が効く。
    await kv.connect()
    await kv.put("a/b", b"v")
    assert await kv.get("a/b") == b"v"
    with pytest.raises(UnsafePathError):
        await kv.put("../escape", b"x")
    # array も Safe 越しに合成キーで使える。
    await arr.connect()
    await arr.put("docs/x", b"d")
    assert await arr.get("docs/x") == b"d"


# ── create（create-if-not-exists・M049） ──────────────────────────────────────


async def test_create_new_key_then_conflict_on_existing() -> None:
    """create は新規なら put 同様に書け、既存キーへは ConflictError（create-if-not-exists）。"""
    from manystore.exceptions import ConflictError

    store = DictKeyValueStore()
    info = await store.create("a/b", b"hello")
    assert (info["filename"], info["size"]) == ("a/b", 5)
    assert await store.get("a/b") == b"hello"
    # 既存キーは ConflictError（値も上書きされない）。
    with pytest.raises(ConflictError):
        await store.create("a/b", b"NEW")
    assert await store.get("a/b") == b"hello"


async def test_create_through_safe_validates_key() -> None:
    """Safe 越しの create も（基底 default が override 済み exists/put を呼ぶ）キー検証が効く。"""
    store = SafeKeyValueStore(DictKeyValueStore())
    await store.create("ok/key", b"v")
    assert await store.get("ok/key") == b"v"
    with pytest.raises(UnsafePathError):
        await store.create("../escape", b"x")


# ── StorageMirror（2 ストア片方向同期・M050） ─────────────────────────────────


async def test_storage_mirror_plan_classifies_create_update_skip_delete() -> None:
    """plan は集合差で create/update/skip と（prune 時の）delete を分類する（適用はしない）。"""
    from manystore import StorageMirror

    source = DictKeyValueStore({"new": b"x", "same": b"AA", "changed": b"AAAA"})
    sink = DictKeyValueStore({"same": b"BB", "changed": b"BB", "orphan": b"Z"})
    mirror = StorageMirror(source, sink)  # 既定 comparator＝size 比較
    plan = await mirror.plan(prune=True)
    assert plan.create == ["new"]  # source のみ
    assert plan.update == ["changed"]  # size 違い（4 vs 2）
    assert plan.skip == ["same"]  # size 一致（2 vs 2）
    assert plan.delete == ["orphan"]  # sink のみ＝prune 対象


async def test_storage_mirror_sync_makes_sink_match_source_without_prune() -> None:
    """prune=False（既定）は orphan を残し source の値で sink を作成/更新（片方向・source 正）。"""
    from manystore import StorageMirror

    src_data = {"new": b"x", "same": b"AA", "changed": b"AAAA"}
    sink_data = {"same": b"BB", "changed": b"BB", "orphan": b"Z"}
    mirror = StorageMirror(DictKeyValueStore(src_data), DictKeyValueStore(sink_data))
    result = await mirror.sync()  # prune 既定 False
    assert result.created == ["new"]
    assert result.updated == ["changed"]
    assert result.skipped == ["same"]
    assert result.deleted == []
    # sink は source の値に一致（orphan は keep）。
    assert sink_data["new"] == b"x"
    assert sink_data["changed"] == b"AAAA"
    assert sink_data["orphan"] == b"Z"


async def test_storage_mirror_sync_prune_removes_orphans() -> None:
    """prune=True で sink にあって source に無いキーを削除＝完全ミラー。"""
    from manystore import StorageMirror

    sink_data = {"keep": b"v", "orphan": b"Z"}
    mirror = StorageMirror(DictKeyValueStore({"keep": b"v"}), DictKeyValueStore(sink_data))
    result = await mirror.sync(prune=True)
    assert result.deleted == ["orphan"]
    assert "orphan" not in sink_data
    assert sink_data["keep"] == b"v"


# ── connect/aclose のロールバック・全件クローズ（M057）＋ nats lazy connect の直列化（M056） ──


class _LifecycleSpy:
    """connect/aclose だけを持つ最小ストア（_connect_all/_aclose_all は両者しか呼ばない）。"""

    def __init__(self, *, fail_connect: bool = False, fail_close: bool = False) -> None:
        self.connected = False
        self.closed = False
        self._fail_connect = fail_connect
        self._fail_close = fail_close

    async def connect(self) -> None:
        if self._fail_connect:
            raise RuntimeError("connect boom")
        self.connected = True

    async def aclose(self) -> None:
        if self._fail_close:
            raise RuntimeError("close boom")
        self.closed = True


async def test_connect_all_rolls_back_established_on_failure() -> None:
    # M057: N 番目の connect 失敗で、確立済み（1..N-1）を巻き戻してから元の例外を再送出する。
    from manystore.protocols import _connect_all

    ok1, ok2, boom = _LifecycleSpy(), _LifecycleSpy(), _LifecycleSpy(fail_connect=True)
    with pytest.raises(RuntimeError, match="connect boom"):
        await _connect_all([ok1, ok2, boom])
    assert ok1.connected and ok1.closed  # 確立済みは巻き戻しで閉じられる
    assert ok2.connected and ok2.closed


async def test_aclose_all_closes_every_store_despite_failure() -> None:
    # M057: 途中の aclose 失敗で残りを閉じ漏らさない（全件試行し最初の例外を送出）。
    from manystore.protocols import _aclose_all

    a, bad, c = _LifecycleSpy(), _LifecycleSpy(fail_close=True), _LifecycleSpy()
    with pytest.raises(RuntimeError, match="close boom"):
        await _aclose_all([a, bad, c])
    assert a.closed and c.closed  # bad の失敗に関わらず前後とも閉じられる


async def test_nats_get_obs_connects_once_under_concurrency(monkeypatch) -> None:
    # M056: 並行 _get_obs が nats.connect を二重に張らない（lazy connect を Lock で直列化）。
    import sys
    import types

    from manystore import NatsObjectKeyValueStore

    connects = 0

    class _FakeObs: ...

    class _FakeJs:
        async def object_store(self, bucket):
            return _FakeObs()

    class _FakeNc:
        def jetstream(self):
            return _FakeJs()

        async def close(self): ...

    async def _connect(url):
        nonlocal connects
        connects += 1
        await asyncio.sleep(0.01)  # 接続の窓を作り、ロック無しなら二重接続が起きるようにする
        return _FakeNc()

    fake_nats = types.ModuleType("nats")
    fake_nats.connect = _connect
    fake_errors = types.ModuleType("nats.js.errors")
    fake_errors.BucketNotFoundError = type("BucketNotFoundError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "nats", fake_nats)
    monkeypatch.setitem(sys.modules, "nats.js", types.ModuleType("nats.js"))
    monkeypatch.setitem(sys.modules, "nats.js.errors", fake_errors)

    store = NatsObjectKeyValueStore(url="nats://x", bucket="b")
    results = await asyncio.gather(*[store._get_obs() for _ in range(10)])
    assert connects == 1  # 10 並行でも接続は 1 回だけ
    assert all(r is results[0] for r in results)  # 全員が同じ obs を得る
