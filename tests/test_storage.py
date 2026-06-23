"""manystore のテスト。

テストは `manystore` パッケージと同階層の `tests/` に置く（パッケージ dir はソースのみ＝
wheel にテストが入らない）。`pytest tests/`（または `make test`）で回す。
"""

import asyncio
import io
from pathlib import Path

import pytest

from manystore import (
    DEFAULT_CACHE_DIR,
    ArrayKeyValueStore,
    AsyncToSyncKeyValueStore,
    ConnectPolicy,
    DownloadCache,
    HttpFileStore,
    HttpKeyValueStore,
    KeyValueFileStore,
    KeyValueFromFileStore,
    LocalFileStore,
    LocalKeyValueStore,
    NatsFileStore,
    NatsObjectKeyValueStore,
    S3FileStore,
    SafeFileStore,
    SafeKeyValueStore,
    UnsafePathError,
    connect_key_value_store,
    connecting,
    create_key_value_store,
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
        assert [i["filename"] for i in store.iter()] == ["b.txt", "a.txt"]
        assert [i["filename"] for i in store.list(limit=1)] == ["b.txt"]
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


def test_safe_kvs_validates_before_delegating(tmp_path: Path) -> None:
    safe = SafeKeyValueStore(LocalKeyValueStore(tmp_path))
    # 正常キー（サブディレクトリ付き）は通り、委譲先に書かれる。
    asyncio.run(safe.put("ok/a.txt", b"hi"))
    assert asyncio.run(safe.get("ok/a.txt")) == b"hi"
    # 不正キーは委譲前に弾く。
    with pytest.raises(UnsafePathError):
        asyncio.run(safe.put("../evil", b"x"))
    with pytest.raises(UnsafePathError):
        asyncio.run(safe.get("/abs"))


def test_local_kvs_iter_and_list(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
        for name in ("a", "b", "c"):
            await store.put(name, name.encode())
        # iter は全件を名前降順で yield する。
        names = [info["filename"] async for info in store.iter()]
        assert names == ["c", "b", "a"]
        # list は iter の先頭 limit 件。
        assert [i["filename"] for i in await store.list(limit=2)] == ["c", "b"]

    asyncio.run(scenario())


def test_local_file_store_open_write_read(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)

    async def scenario() -> None:
        # 書き込みモードは親ディレクトリを作って open できる。
        async with await store.open_writer("d/f.bin") as f:
            await f.write(b"hello")
        # 読み込みは open→read→（context manager で close）。
        async with await store.open_reader("d/f.bin") as f:
            assert await f.read() == b"hello"

    asyncio.run(scenario())


def test_local_kvs_put_creates_parent_dirs(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)
    # '/' を含むキーは親ディレクトリを作って格納できる（s3/nats のフラットキー規約に整合）。
    asyncio.run(store.put("a/b/c.bin", b"data"))
    assert asyncio.run(store.get("a/b/c.bin")) == b"data"


def test_local_kvs_delete_removes_file_keeps_dirs(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
        await store.put("a/b.txt", b"x")
        assert await store.exists("a/b.txt")
        await store.delete("a/b.txt")
        assert not await store.exists("a/b.txt")
        # ファイルだけ消す。親ディレクトリは残す。
        assert (tmp_path / "a").is_dir()
        # 無いキーの delete は無視（例外を投げない）。
        await store.delete("missing")

    asyncio.run(scenario())


def test_local_kvs_vacuum_removes_empty_dirs(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
        await store.put("a/b/c.txt", b"x")
        await store.put("keep/d.txt", b"y")
        await store.delete("a/b/c.txt")  # a/b は空になるが delete は残す
        assert (tmp_path / "a" / "b").is_dir()
        await store.vacuum()
        # ネストした空ディレクトリ（a, a/b）は畳まれ、中身のある keep は残る。
        assert not (tmp_path / "a").exists()
        assert (tmp_path / "keep").is_dir()

    asyncio.run(scenario())


def test_local_kvs_cp_and_mv(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
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
        with pytest.raises(FileNotFoundError):
            await store.cp("missing", "x")
        with pytest.raises(FileNotFoundError):
            await store.mv("missing", "x")

    asyncio.run(scenario())


def test_local_kvs_put_is_atomic(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
        await store.put("k", b"v1")
        await store.put("k", b"v2")  # 原子的に差し替え
        assert await store.get("k") == b"v2"
        # 一時ファイルの残骸が無い（最終ファイルだけ）。
        assert [p.name for p in tmp_path.iterdir()] == ["k"]

    asyncio.run(scenario())


def test_local_file_store_write_is_atomic_on_error(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)

    async def scenario() -> None:
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

    asyncio.run(scenario())


def test_local_kvs_iter_is_recursive(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
        await store.put("top.txt", b"1")
        await store.put("a/b/c.bin", b"2")
        # iter はサブディレクトリ配下のキーも相対 posix パスで列挙する。
        names = [info["filename"] async for info in store.iter()]
        assert names == ["top.txt", "a/b/c.bin"]  # 名前降順

    asyncio.run(scenario())


def test_key_value_file_store_open_over_kvs(tmp_path: Path) -> None:
    # KeyValueStore を FileStore として被せる（s3/nats も同型で FileStore 化できる）。
    fs = KeyValueFileStore(LocalKeyValueStore(tmp_path))

    async def scenario() -> None:
        async with await fs.open_writer("k/v.bin") as f:
            await f.write(b"abc")
            await f.write(b"de")  # close 時にまとめて put
        async with await fs.open_reader("k/v.bin") as f:
            assert await f.read() == b"abcde"
        # 無いキーの読み取りは FileNotFoundError。
        with pytest.raises(FileNotFoundError):
            await fs.open_reader("missing")

    asyncio.run(scenario())


def test_kvs_get_default_and_get_or_raise(tmp_path: Path) -> None:
    # get は欠損時にデフォルト値（既定 None）を返し、get_or_raise は FileNotFoundError を上げる。
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
        # 欠損キー
        assert await store.get("missing") is None  # 既定デフォルト
        assert await store.get("missing", b"fallback") == b"fallback"  # 明示デフォルト
        with pytest.raises(FileNotFoundError):
            await store.get_or_raise("missing")
        # 存在キーは default を無視して実値を返す（get / get_or_raise とも）。
        await store.put("k", b"v")
        assert await store.get("k", b"fallback") == b"v"
        assert await store.get_or_raise("k") == b"v"

    asyncio.run(scenario())


def test_local_file_store_is_full_kvs(tmp_path: Path) -> None:
    # FileStore = KeyValueStore + IO。LocalFileStore は IO を持ちつつ KVS としても完全に働く。
    fs = LocalFileStore(tmp_path)

    async def scenario() -> None:
        # KVS 面: put / get(default) / get_or_raise / iter
        await fs.put("a/b.bin", b"hello")
        assert await fs.get("a/b.bin") == b"hello"
        assert await fs.get("missing") is None
        assert await fs.get("missing", b"def") == b"def"
        with pytest.raises(FileNotFoundError):
            await fs.get_or_raise("missing")
        assert [i["filename"] async for i in fs.iter()] == ["a/b.bin"]
        # IO 面: open_reader でも同じ真実が読める
        async with await fs.open_reader("a/b.bin") as r:
            assert await r.read() == b"hello"

    asyncio.run(scenario())


def test_key_value_file_store_is_full_file_store(tmp_path: Path) -> None:
    # KVS→FileStore は IO の埋め合わせ＝KVS 面は委譲しつつ open_reader/open_writer を合成。
    fs = KeyValueFileStore(LocalKeyValueStore(tmp_path))

    async def scenario() -> None:
        # IO 面（合成）
        async with await fs.open_writer("k.bin") as w:
            await w.write(b"xyz")
        async with await fs.open_reader("k.bin") as r:
            assert await r.read() == b"xyz"
        # KVS 面（下層へ委譲）も使える＝完全な FileStore
        assert await fs.get_or_raise("k.bin") == b"xyz"
        assert await fs.get("missing", b"d") == b"d"
        assert [i["filename"] async for i in fs.iter()] == ["k.bin"]
        # 欠損キーの open_reader は FileNotFoundError（get_or_raise 経由）
        with pytest.raises(FileNotFoundError):
            await fs.open_reader("missing")

    asyncio.run(scenario())


def test_key_value_from_file_store_derives_kvs(tmp_path: Path) -> None:
    # FileStore を KVS として被せる逆向きアダプタ（IO を落とすだけ・残りは下層へ委譲）。
    kv = KeyValueFromFileStore(LocalFileStore(tmp_path))

    async def scenario() -> None:
        await kv.put("a/b.bin", b"hello")  # 親ディレクトリは下層 writer が作る
        assert await kv.get("a/b.bin") == b"hello"
        assert await kv.get("missing") is None  # 欠損キーは None（KVS 規約）
        assert await kv.exists("a/b.bin") is True
        names = [info["filename"] async for info in kv.iter()]
        assert names == ["a/b.bin"]
        await kv.cp("a/b.bin", "c.bin")
        assert await kv.get("c.bin") == b"hello"
        await kv.mv("c.bin", "d.bin")
        assert await kv.exists("c.bin") is False
        assert await kv.get("d.bin") == b"hello"
        await kv.delete("a/b.bin")
        assert await kv.exists("a/b.bin") is False

    asyncio.run(scenario())


def test_local_kvs_is_thin_view_over_file_store(tmp_path: Path) -> None:
    # LocalKeyValueStore は LocalFileStore を被せた薄い KVS ビュー（実装は FileStore 側に集約）。
    store = LocalKeyValueStore(tmp_path)
    assert isinstance(store, KeyValueFromFileStore)

    async def scenario() -> None:
        # KVS 経由の put は、同じ dir の FileStore から open_reader でも読める（同一の真実）。
        await store.put("shared.bin", b"xyz")
        fs = LocalFileStore(tmp_path)
        async with await fs.open_reader("shared.bin") as f:
            assert await f.read() == b"xyz"

    asyncio.run(scenario())


def test_safe_file_store_validates_filename(tmp_path: Path) -> None:
    safe = SafeFileStore(LocalFileStore(tmp_path))

    async def scenario() -> None:
        async with await safe.open_writer("ok/a.bin") as f:
            await f.write(b"x")
        async with await safe.open_reader("ok/a.bin") as f:
            assert await f.read() == b"x"
        with pytest.raises(UnsafePathError):
            await safe.open_reader("../evil")

    asyncio.run(scenario())


def test_local_kvs_path_fixed_at_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 相対パスで初期化しても、初期化時の cwd を基準に絶対パスへ固定される。
    monkeypatch.chdir(tmp_path)
    (tmp_path / "store").mkdir()
    store = LocalKeyValueStore(Path("store"))
    asyncio.run(store.put("k", b"v"))
    # 実行中に cd しても、初期化時に固定したパスを参照する。
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert asyncio.run(store.get("k")) == b"v"
    assert (tmp_path / "store" / "k").read_bytes() == b"v"


# ── S3 streaming file store（fake S3 client で分割ロジックを検証） ──


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read() if size is None or size < 0 else self._buf.read(size)

    def close(self) -> None:
        self._buf.close()

    async def __aenter__(self) -> _FakeBody:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._buf.close()


class _FakeS3:
    """S3FileStore を駆動する最小のインメモリ fake（async client 兼 context manager）。"""

    class exceptions:  # noqa: N801  aiobotocore の client.exceptions.NoSuchKey 形に合わせる
        class NoSuchKey(Exception): ...

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self._uploads: dict[str, dict] = {}
        self._uid = 0

    async def __aenter__(self) -> _FakeS3:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def create_multipart_upload(self, Bucket: str, Key: str) -> dict:
        self._uid += 1
        uid = f"u{self._uid}"
        self._uploads[uid] = {"key": Key, "parts": {}}
        return {"UploadId": uid}

    async def upload_part(self, Bucket, Key, PartNumber, UploadId, Body) -> dict:
        self._uploads[UploadId]["parts"][PartNumber] = bytes(Body)
        return {"ETag": f'"etag{PartNumber}"'}

    async def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload) -> dict:
        up = self._uploads.pop(UploadId)
        order = [p["PartNumber"] for p in MultipartUpload["Parts"]]
        self.objects[Key] = b"".join(up["parts"][n] for n in order)
        return {}

    async def put_object(self, Bucket, Key, Body) -> dict:
        self.objects[Key] = bytes(Body)
        return {}

    async def get_object(self, Bucket, Key) -> dict:
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey  # 欠損は NoSuchKey（実 client と同形）
        return {"Body": _FakeBody(self.objects[Key])}


def test_s3_file_store_streams_multipart_write_and_read() -> None:
    fake = _FakeS3()
    store = S3FileStore("bucket", part_size=4)  # 小さなパートで分割を起こす
    store._session = lambda: fake  # 接続を fake に差し替え

    async def scenario() -> None:
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

    asyncio.run(scenario())


def test_s3_file_store_is_full_kvs() -> None:
    # S3FileStore = S3KeyValueStore + streaming IO。KVS 面（whole get/put）も持つ完全 FileStore。
    fake = _FakeS3()
    store = S3FileStore("bucket")
    store._session = lambda: fake

    async def scenario() -> None:
        await store.put("kv", b"data")  # 継承 put＝put_object（whole）
        assert fake.objects["kv"] == b"data"
        assert await store.get("kv") == b"data"  # 継承 get（whole get_object）
        assert await store.get_or_raise("kv") == b"data"
        assert await store.get("missing") is None  # 欠損は default
        assert await store.get("missing", b"d") == b"d"
        with pytest.raises(FileNotFoundError):
            await store.get_or_raise("missing")

    asyncio.run(scenario())


# ── NATS backend（fake object store。実 nats-py の API 形に合わせる） ──


class _FakeObjResult:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeObjInfo:
    def __init__(self, name: str, size: int, deleted: bool = False) -> None:
        self.name = name
        self.size = size
        self.deleted = deleted


class _FakeNatsObs:
    """最小の fake object store（nats-py の get/get_info/put/delete/list に合わせる）。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, name: str, data, meta=None) -> None:
        self.objects[name] = bytes(data)

    async def get(self, name: str, writeinto=None, show_deleted=False) -> _FakeObjResult:
        if name not in self.objects:
            raise KeyError(name)  # ObjectNotFound 相当
        return _FakeObjResult(self.objects[name])

    async def get_info(self, name: str, show_deleted=False) -> _FakeObjInfo:
        if name not in self.objects:
            raise KeyError(name)
        return _FakeObjInfo(name, len(self.objects[name]))

    async def delete(self, name: str) -> None:
        self.objects.pop(name, None)

    async def list(self, ignore_deletes=False) -> list[_FakeObjInfo]:
        return [_FakeObjInfo(n, len(v)) for n, v in self.objects.items()]


def _patch_obs(store, fake: _FakeNatsObs) -> None:
    async def fake_get_obs() -> _FakeNatsObs:
        return fake

    store._get_obs = fake_get_obs


def test_nats_file_store_buffered_read_write() -> None:
    store = NatsFileStore("nats://x", "bucket")
    fake = _FakeNatsObs()
    _patch_obs(store, fake)

    async def scenario() -> None:
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

        # 無いキーは FileNotFoundError。
        with pytest.raises(FileNotFoundError):
            await store.open_reader("missing")

    asyncio.run(scenario())


def test_nats_file_store_is_full_kvs() -> None:
    # NatsFileStore = NatsObjectKeyValueStore + buffer 合成 IO。KVS 面も使える完全な FileStore。
    store = NatsFileStore("nats://x", "bucket")
    fake = _FakeNatsObs()
    _patch_obs(store, fake)

    async def scenario() -> None:
        await store.put("kv", b"data")  # 継承 put（whole）
        assert fake.objects["kv"] == b"data"
        assert await store.get("kv") == b"data"  # 継承 get（whole）
        assert await store.get_or_raise("kv") == b"data"
        assert await store.get("missing") is None
        with pytest.raises(FileNotFoundError):
            await store.get_or_raise("missing")
        assert await store.exists("kv") is True
        assert [i["filename"] async for i in store.iter()] == ["kv"]

    asyncio.run(scenario())


def test_nats_kvs_exists_uses_get_info() -> None:
    # exists は get_info を使う（ObjectStore に info は無い）。
    store = NatsObjectKeyValueStore("nats://x", "bucket")
    fake = _FakeNatsObs()
    _patch_obs(store, fake)

    async def scenario() -> None:
        assert await store.exists("k") is False
        await store.put("k", b"v")
        assert await store.exists("k") is True

    asyncio.run(scenario())


# ── connection lifecycle（接続前 factory 包み → async with で接続） ──


def test_connect_key_value_store_local_roundtrip(tmp_path: Path) -> None:
    async def scenario() -> None:
        # init では接続せず、async with で connect してから使う。
        async with connect_key_value_store("local", local_dir=tmp_path) as store:
            await store.put("k", b"v")
            assert await store.get("k") == b"v"

    asyncio.run(scenario())


def test_local_kvs_connect_aclose_lifecycle(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)

    async def scenario() -> None:
        await store.connect()  # ローカルは体裁上のステップ（dir を用意するだけ）
        await store.put("k", b"v")
        await store.aclose()

    asyncio.run(scenario())


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


def test_connecting_verify_true_raises_and_acloses() -> None:
    bad = _BadConnectStore()

    async def scenario() -> None:
        with pytest.raises(ConnectionError):
            async with connecting(lambda: bad, policy=_NO_RETRY):  # verify=True 既定
                pass

    asyncio.run(scenario())
    assert bad.aclosed is True  # 失敗時も後始末される


def test_connecting_verify_false_ignores_failure() -> None:
    bad = _BadConnectStore()

    async def scenario() -> None:
        # verify=False は初回接続失敗を無視して中へ入れる。
        async with connecting(lambda: bad, verify=False, policy=_NO_RETRY) as store:
            assert store is bad

    asyncio.run(scenario())


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


def test_retry_until_success() -> None:
    flaky = _FlakyConnectStore(fail_times=2)

    async def scenario() -> None:
        async with connecting(lambda: flaky, policy=ConnectPolicy(max_retry=2, delay=0)) as s:
            assert s is flaky

    asyncio.run(scenario())
    assert flaky.attempts == 3  # 2 回失敗 + 1 回成功（max_retry=2 → 総 3 試行）


def test_retry_exhausted_raises() -> None:
    flaky = _FlakyConnectStore(fail_times=5)

    async def scenario() -> None:
        with pytest.raises(ConnectionError):
            async with connecting(lambda: flaky, policy=ConnectPolicy(max_retry=1, delay=0)):
                pass

    asyncio.run(scenario())
    assert flaky.attempts == 2  # max_retry=1 → 総 2 試行で打ち切り
    assert flaky.aclosed is True


def test_retry_timeout_per_attempt() -> None:
    class _HangStore:
        async def connect(self) -> None:
            await asyncio.sleep(10)  # 1 回の connect がハング

        async def aclose(self) -> None:
            return None

    policy = ConnectPolicy(max_retry=0, timeout=0.01)

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            async with connecting(lambda: _HangStore(), policy=policy):
                pass

    asyncio.run(scenario())


def test_retry_deadline_bounds_hung_connect_without_timeout() -> None:
    class _HangStore:
        async def connect(self) -> None:
            await asyncio.sleep(10)  # ハングする connect

        async def aclose(self) -> None:
            return None

    # timeout=None でも deadline があれば 1 回のハングで無限待機しない。
    policy = ConnectPolicy(max_retry=0, timeout=None, deadline=0.02)

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            async with connecting(lambda: _HangStore(), policy=policy):
                pass

    asyncio.run(scenario())


def test_retry_deadline_stops_early() -> None:
    flaky = _FlakyConnectStore(fail_times=100)

    async def scenario() -> None:
        with pytest.raises(ConnectionError):
            async with connecting(
                lambda: flaky,
                policy=ConnectPolicy(max_retry=float("inf"), delay=0.02, deadline=0.05),
            ):
                pass

    asyncio.run(scenario())
    assert flaky.attempts < 100  # deadline で attempts 到達前に打ち切り


def test_retry_verify_false_ignores_after_exhaustion() -> None:
    flaky = _FlakyConnectStore(fail_times=100)

    async def scenario() -> None:
        # 粘っても駄目だが verify=False なので無視して中へ。
        async with connecting(
            lambda: flaky, verify=False, policy=ConnectPolicy(max_retry=1, delay=0)
        ) as s:
            assert s is flaky

    asyncio.run(scenario())
    assert flaky.attempts == 2


# ── download cache（KeyValueStore → ローカルキャッシュ） ──


def test_download_cache_fetches_caches_and_force(tmp_path: Path) -> None:
    upstream = LocalKeyValueStore(tmp_path / "remote")  # 上流ストア（リモート相当）
    cache = DownloadCache(upstream, cache_dir=tmp_path / "cache")

    async def scenario() -> None:
        await upstream.put("m/model.bin", b"weights")
        p = await cache.download("m/model.bin")
        assert p == tmp_path / "cache" / "m" / "model.bin"
        assert p.read_bytes() == b"weights"

        # 上流を変えてもキャッシュ済みなら再取得しない（存在ベース）。
        await upstream.put("m/model.bin", b"CHANGED")
        assert (await cache.download("m/model.bin")).read_bytes() == b"weights"
        # force=True で取り直す。
        assert (await cache.download("m/model.bin", force=True)).read_bytes() == b"CHANGED"

        # 上流に無ければ FileNotFoundError。
        with pytest.raises(FileNotFoundError):
            await cache.download("missing")

    asyncio.run(scenario())


def test_download_cache_rejects_unsafe_key(tmp_path: Path) -> None:
    cache = DownloadCache(LocalKeyValueStore(tmp_path / "r"), cache_dir=tmp_path / "c")

    async def scenario() -> None:
        with pytest.raises(UnsafePathError):
            await cache.download("../evil")  # キャッシュ外へ書かせない

    asyncio.run(scenario())


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


def test_array_kvs_mount_and_route(tmp_path: Path) -> None:
    arr = ArrayKeyValueStore()

    async def scenario() -> None:
        await arr.mount("docs", LocalKeyValueStore(tmp_path / "docs"))
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
        names = sorted([i["filename"] async for i in arr.iter()])
        assert names == ["docs/a.txt", "imgs/p/q.bin"]
        # 未知 mount / 形式不正はエラー。
        with pytest.raises(KeyError):
            await arr.get("unknown/x")
        with pytest.raises(KeyError):
            await arr.get("docs")  # subkey 無し
        # delete も振り分け。
        await arr.delete("docs/a.txt")
        assert await arr.exists("docs/a.txt") is False

    asyncio.run(scenario())


def test_array_kvs_cp_mv_across_mounts(tmp_path: Path) -> None:
    arr = ArrayKeyValueStore()

    async def scenario() -> None:
        await arr.mount("a", LocalKeyValueStore(tmp_path / "a"))
        await arr.mount("b", LocalKeyValueStore(tmp_path / "b"))
        await arr.put("a/x", b"data")
        await arr.cp("a/x", "b/y")  # mount 跨ぎ copy
        assert await arr.get("b/y") == b"data"
        assert await arr.get("a/x") == b"data"  # src は残る
        await arr.mv("a/x", "b/z")  # mount 跨ぎ move
        assert await arr.get("a/x") is None
        assert await arr.get("b/z") == b"data"

    asyncio.run(scenario())


def test_array_mount_connects_backend() -> None:
    class _ConnectRecorder:
        def __init__(self) -> None:
            self.connected = False

        async def connect(self) -> None:
            self.connected = True

        async def aclose(self) -> None:
            return None

    arr = ArrayKeyValueStore()
    rec = _ConnectRecorder()

    async def scenario() -> None:
        await arr.mount("x", rec)  # mount 時に backend を connect する
        assert rec.connected is True

    asyncio.run(scenario())


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


def test_http_kvs_get_and_exists() -> None:
    objects = {"a.txt": b"hello", "dir/b.bin": b"\x00\x01"}
    store = HttpKeyValueStore(base_url=_HTTP_BASE)
    store._client = lambda: _FakeHttpClient(objects, _HTTP_BASE)  # 接続を fake に差し替え

    async def scenario() -> None:
        assert await store.get("a.txt") == b"hello"
        assert await store.get("dir/b.bin") == b"\x00\x01"  # 多段キーも base_url 相対で取れる
        assert await store.get("missing") is None  # 404 → None
        assert await store.exists("a.txt") is True
        assert await store.exists("missing") is False

    asyncio.run(scenario())


def test_http_file_store_is_full_read_only_kvs() -> None:
    # HttpFileStore = HttpKeyValueStore + read IO。KVS 面（read-only）も持つ完全な FileStore。
    objects = {"a.txt": b"hello"}
    store = HttpFileStore(base_url=_HTTP_BASE)
    store._client = lambda: _FakeHttpClient(objects, _HTTP_BASE)

    async def scenario() -> None:
        # read IO（whole get の buffer 合成）
        async with await store.open_reader("a.txt") as f:
            assert await f.read() == b"hello"
        with pytest.raises(FileNotFoundError):
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

    asyncio.run(scenario())


def test_http_kvs_is_read_only() -> None:
    store = HttpKeyValueStore(base_url=_HTTP_BASE)

    async def scenario() -> None:
        with pytest.raises(io.UnsupportedOperation):
            await store.put("k", b"v")
        with pytest.raises(io.UnsupportedOperation):
            await store.delete("k")
        with pytest.raises(io.UnsupportedOperation):
            await store.cp("a", "b")
        with pytest.raises(io.UnsupportedOperation):
            await store.mv("a", "b")
        with pytest.raises(io.UnsupportedOperation):
            await store.list()
        with pytest.raises(io.UnsupportedOperation):
            async for _ in store.iter():
                pass

    asyncio.run(scenario())


def test_http_file_store_read() -> None:
    objects = {"a.txt": b"hello"}
    fs = HttpFileStore(base_url=_HTTP_BASE)
    fs._client = lambda: _FakeHttpClient(objects, _HTTP_BASE)

    async def scenario() -> None:
        async with await fs.open_reader("a.txt") as f:
            assert await f.read() == b"hello"
        with pytest.raises(FileNotFoundError):  # 404 → FileNotFoundError
            await fs.open_reader("missing")
        with pytest.raises(io.UnsupportedOperation):  # write は非対応
            await fs.open_writer("a.txt")

    asyncio.run(scenario())


def test_create_key_value_store_http_wiring() -> None:
    # ファクトリ経由で backend="http" → HttpKeyValueStore が組み立つ（base_url/headers を渡す）。
    store = create_key_value_store(
        "http", http_base_url=_HTTP_BASE, http_headers={"Authorization": "Bearer t"}
    )
    assert isinstance(store, HttpKeyValueStore)
    assert store._base_url == _HTTP_BASE
    assert store._headers == {"Authorization": "Bearer t"}
