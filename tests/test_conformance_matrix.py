"""挙動契約 × 実装の**集約ハーネス**（M066）。

`conformance_providers.all_providers()` が宣言する全 provider（Dict / Local / Remote / 実 nats・s3）
に同一の契約 body を流す。IF が揃うので「注入するストアだけ変えて全実装を確認」が 1 か所で回る。

全契約とも自分の uuid 名前空間にだけ触れる**非破壊**＝**全 provider**で流せる（M066①）:
- **CRUD / writer all-or-nothing / 並行 CAS** … 自分の uuid キーのみ。
- **run_light / run_middle**（差分検証）… uuid 名前空間に閉じて操作し後始末する（全消去しない）。

実 backend（gated）は未到達なら skip・`slow` マーク・環境/認証未整備の実行時エラーも skip 扱い
（CI など backend 無し環境で赤くしない。`make e2e-up` で起動して `make test-heavy` で実走）。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from conformance_providers import Provider, all_providers, leaf_fault_providers

from manystore import DictFileStore
from manystore.tools.conformancer import (
    FileStoreTester,
    assert_fail_loud_over_transport,
    assert_put_if_absent_concurrency_safe,
    assert_put_if_match_concurrency_safe,
    assert_writer_aborts_on_error,
)

_ALL = all_providers()
_LEAF_FAULT = leaf_fault_providers()


def _params(providers: list[Provider]) -> list:
    # 実 backend（gated）は slow マークで内ループ（make test）から除外。
    return [
        pytest.param(p, id=p.id, marks=[pytest.mark.slow] if p.gated else []) for p in providers
    ]


@asynccontextmanager
async def _store(provider: Provider) -> AsyncIterator[object]:
    """provider を開く。未到達は skip、gated の環境/認証未整備エラーも skip（非 gated は伝播）。"""
    if not provider.reachable():
        pytest.skip(f"{provider.id}: backend 未到達")
    try:
        async with provider.open() as fs:
            yield fs
    except pytest.skip.Exception:
        raise
    except Exception as e:  # noqa: BLE001  gated は環境未整備を skip 扱い（実バグは非 gated で捕まる）
        if provider.gated:
            pytest.skip(f"{provider.id}: 環境/認証 未整備 → {type(e).__name__}: {e}")
        raise


# ── 非破壊契約（全 provider） ──


@pytest.mark.parametrize("provider", _params(_ALL))
async def test_crud_roundtrip(provider: Provider) -> None:
    # put→get→exists→list→cp→delete を全実装で。uuid キーのみ触り後始末する（非破壊）。
    import uuid

    async with _store(provider) as fs:
        key = f"_matrix/{uuid.uuid4().hex}"
        payload = b"hello-manystore"
        await fs.put(key, payload)
        assert await fs.get(key) == payload
        assert await fs.exists(key) is True
        assert key in [info["filename"] for info in await fs.list_all(limit=100)]
        dst = key + ".cp"
        await fs.cp(key, dst)
        assert await fs.get(dst) == payload
        await fs.delete(key)
        await fs.delete(dst)
        assert await fs.exists(key) is False


@pytest.mark.parametrize("provider", _params(_ALL))
async def test_writer_aborts_on_error(provider: Provider) -> None:
    # writer all-or-nothing（例外時は中途バッファを確定しない）。自分の uuid キーのみ（非破壊）。
    async with _store(provider) as fs:
        await assert_writer_aborts_on_error(fs)


@pytest.mark.parametrize("provider", _params(_ALL))
async def test_put_if_absent_concurrency(provider: Provider) -> None:
    # create-only put の並行安全性（一方だけ成功・他方 ConflictError）。uuid キーのみ（非破壊）。
    async with _store(provider) as fs:
        await assert_put_if_absent_concurrency_safe(fs, size=4096)


@pytest.mark.parametrize("provider", _params(_ALL))
async def test_put_if_match_concurrency(provider: Provider) -> None:
    # update CAS の並行安全性（lost-update を ConflictError で拒否）。uuid キーのみ（非破壊）。
    async with _store(provider) as fs:
        await assert_put_if_match_concurrency_safe(fs, size=4096)


@pytest.mark.parametrize("provider", _params(_LEAF_FAULT))
async def test_fail_loud_over_transport(provider: Provider) -> None:
    # leaf backend（nats/s3）を故障 transport に繋ぎ、全 op が障害を握り潰さず loud に失敗するか。
    # 欠損/False/default/正常終了に化けさせない＝M054/M055 クラスを実 backend で検出する。
    async with _store(provider) as store:
        await assert_fail_loud_over_transport(store)


# ── 差分契約（run_light / run_middle ＝非破壊なので全 provider） ──


@pytest.mark.parametrize("provider", _params(_ALL))
async def test_run_light_matches_oracle(provider: Provider) -> None:
    # 辞書ストアを正に open_reader/open_writer/exists/list_all/iter_all を差分検証。
    async with _store(provider) as fs:
        tester = FileStoreTester(DictFileStore(), fs)
        report: list = []
        await tester.run_light(report)
        assert all(s["passed"] for s in report), report


@pytest.mark.parametrize("provider", _params(_ALL))
async def test_run_middle_matches_oracle(provider: Provider) -> None:
    # delete/冪等/複数キー/read 境界/overwrite 縮小の細かい契約を差分検証。
    async with _store(provider) as fs:
        tester = FileStoreTester(DictFileStore(), fs)
        report: list = []
        await tester.run_middle(report)
        assert all(s["passed"] for s in report), report


@pytest.mark.parametrize("provider", _params(_ALL))
async def test_run_heavy_matches_oracle(provider: Provider) -> None:
    # 多チャンク大容量/分割 read/多キー/連続 overwrite の規模・境界契約を差分検証。
    async with _store(provider) as fs:
        tester = FileStoreTester(DictFileStore(), fs)
        report: list = []
        await tester.run_heavy(report)
        assert all(s["passed"] for s in report), report
