"""implement 層のテスト（HTTP 非依存）。config 解釈 / StorageService の CRUD / PollingWatcher。

pytest-asyncio（asyncio_mode=auto）で `async def test_*` をそのまま回す。
"""

import asyncio
from pathlib import Path

import pytest

from manystore.backends import LocalKeyValueStore
from manystore.implement.config import parse_config
from manystore.implement.service import (
    ContextNotFound,
    ReadOnlyContext,
    StorageService,
)
from manystore.implement.watcher import PollingWatcher


def _config(tmp_path: Path) -> object:
    return parse_config(
        {
            "contexts": {
                "work": {"backend": "local", "root": str(tmp_path / "work")},
                "ro": {"backend": "local", "root": str(tmp_path / "ro"), "writable": False},
            },
            "views": {
                "featured": [
                    {
                        "context": "work",
                        "path": "interrupt",
                        "label": "Interrupt",
                        "pin": True,
                        "quick_write": True,
                    }
                ]
            },
            "default_context": "work",
        }
    )


def test_parse_config_normalizes_local_root(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    assert cfg.default_context == "work"
    assert cfg.contexts["work"].backend == "local"
    # root → local_dir(Path) に正規化される。
    assert isinstance(cfg.contexts["work"].opts["local_dir"], Path)
    assert cfg.contexts["ro"].writable is False
    assert cfg.featured[0].context == "work"
    assert cfg.featured[0].quick_write is True


async def test_service_crud_and_featured(tmp_path: Path) -> None:
    service = StorageService(_config(tmp_path))
    await service.connect()
    try:
        # contexts / featured / default が protocol 通りに見える。
        names = {c.name for c in service.list_contexts()}
        assert names == {"work", "ro"}
        assert service.default_context == "work"
        assert service.featured()[0]["label"] == "Interrupt"

        # CRUD（interrupt 投入も「featured な local への put」として汎用 put で成立）。
        await service.put("work", "interrupt/note.md", b"hello")
        assert await service.exists("work", "interrupt/note.md")
        assert await service.get("work", "interrupt/note.md") == b"hello"

        entries = await service.list_entries("work", prefix="interrupt/")
        assert [e.key for e in entries] == ["interrupt/note.md"]
        assert await service.list_entries("work", prefix="nope/") == []

        await service.delete("work", "interrupt/note.md")
        assert await service.get("work", "interrupt/note.md") is None
    finally:
        await service.aclose()


async def test_contexts_are_isolated_first_segment(tmp_path: Path) -> None:
    # context = ArrayStorage の第一階層。同じ key を別 context に入れても混ざらず、
    # list_entries は対象 context だけを切り出す（routing は ArrayStorage 委譲）。
    cfg = parse_config(
        {
            "contexts": {
                "a": {"backend": "local", "root": str(tmp_path / "a")},
                "b": {"backend": "local", "root": str(tmp_path / "b")},
            }
        }
    )
    service = StorageService(cfg)
    await service.connect()
    try:
        await service.put("a", "k.txt", b"AA")
        await service.put("b", "k.txt", b"BB")
        assert await service.get("a", "k.txt") == b"AA"
        assert await service.get("b", "k.txt") == b"BB"
        # 各 context の一覧は自分の key だけ（他 context は混ざらない）。
        assert [e.key for e in await service.list_entries("a")] == ["k.txt"]
        assert [e.key for e in await service.list_entries("b")] == ["k.txt"]
        # 一方を消しても他方は残る。
        await service.delete("a", "k.txt")
        assert await service.get("a", "k.txt") is None
        assert await service.get("b", "k.txt") == b"BB"
    finally:
        await service.aclose()


async def test_service_readonly_and_unknown_context(tmp_path: Path) -> None:
    service = StorageService(_config(tmp_path))
    await service.connect()
    try:
        with pytest.raises(ReadOnlyContext):
            await service.put("ro", "a.txt", b"x")
        with pytest.raises(ContextNotFound):
            await service.get("missing", "a.txt")
    finally:
        await service.aclose()


@pytest.mark.slow  # ポーリング検知を asyncio.sleep で待つ＝待ち支配（R13）
async def test_polling_watcher_detects_changes(tmp_path: Path) -> None:
    store = LocalKeyValueStore(tmp_path)
    watcher = PollingWatcher(store, "work", interval=0.05)
    await watcher.start()
    gen = watcher.subscribe()
    # 購読を先に登録してから変更を起こす（イベントの取りこぼし防止）。
    first = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.02)
    await store.put("a.txt", b"hi")
    ev = await asyncio.wait_for(first, 2.0)
    assert ev.type == "created"
    assert ev.key == "a.txt"

    # modified（サイズ変化）も拾う。
    nxt = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.02)
    await store.put("a.txt", b"hi there")
    ev2 = await asyncio.wait_for(nxt, 2.0)
    assert ev2.type == "modified"

    await gen.aclose()
    await watcher.aclose()
