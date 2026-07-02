"""watcher — context（ストア）の変更を監視してイベント列にする。

MVP は [PollingWatcher]（純 stdlib・全 backend 対応・決定的でテスト容易）。ストアを一定間隔で
列挙し、前回スナップショットとの差分を `created` / `modified` / `deleted` イベントにする。
1 つの watcher を複数 WS 購読へ fan-out する（背後のポーリングは 1 本）。

> inotify（watchdog）ベースの LocalWatcher は最適化として後続（local backend のみ）。WS の
> ライブ通知という要件自体は polling 起点でも満たせる。`modified` は size 変化で検出するため、
> 同一サイズでの編集は取りこぼし得る（既知の制約。将来 mtime/hash で補強）。
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Protocol

from ...protocols import AsyncBufferedStore
from .protocol import Event


class Watcher(Protocol):
    """変更監視の抽象。`subscribe()` で購読し、`aclose()` で停止する。"""

    def subscribe(self) -> AsyncIterator[Event]: ...
    async def aclose(self) -> None: ...


async def _snapshot(store: AsyncBufferedStore) -> dict[str, int]:
    """ストアを列挙して `key -> size` のスナップショットを作る。"""
    snap: dict[str, int] = {}
    async for info in store.iter_all():
        snap[info["filename"]] = info["size"]
    return snap


def _diff(old: dict[str, int], new: dict[str, int], context: str) -> list[Event]:
    """2 つのスナップショットの差分をイベント列にする。"""
    events: list[Event] = []
    for key, size in new.items():
        if key not in old:
            events.append(Event(type="created", context=context, key=key))
        elif old[key] != size:
            events.append(Event(type="modified", context=context, key=key))
    for key in old:
        if key not in new:
            events.append(Event(type="deleted", context=context, key=key))
    return events


class PollingWatcher:
    """ストアを一定間隔で列挙し、差分イベントを購読者へ fan-out するウォッチャ。"""

    def __init__(self, store: AsyncBufferedStore, context: str, interval: float = 1.0) -> None:
        self._store = store
        self._context = context
        self._interval = interval
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._task: asyncio.Task[None] | None = None
        self._snapshot: dict[str, int] = {}

    async def start(self) -> None:
        """ベースラインのスナップショットを取り、ポーリングループを起動する。

        起動時点の中身は「既存」として扱い（イベントを出さない）、以降の変更だけを通知する。
        """
        if self._task is not None:
            return
        self._snapshot = await _snapshot(self._store)
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                new = await _snapshot(self._store)
            except Exception:
                # 一時的な列挙失敗（接続断など）はスキップして次のポーリングへ。
                continue
            events = _diff(self._snapshot, new, self._context)
            self._snapshot = new
            for ev in events:
                for q in list(self._subscribers):
                    q.put_nowait(ev)

    async def subscribe(self) -> AsyncIterator[Event]:
        """以降の変更イベントを 1 件ずつ yield する非同期イテレータ。"""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def aclose(self) -> None:
        """ポーリングループを止める。"""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
