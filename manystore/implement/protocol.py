"""protocol — server / client が共有する契約の型（dataclass）。

fastapi/pydantic に依存しない素の dataclass にする（implement 層は HTTP 非依存）。
server 層はこれを JSON にシリアライズして返し、client 層は JSON からこれを組み立てる。
"""

from dataclasses import dataclass
from typing import Literal

# 変更イベントの種別。
EventType = Literal["created", "modified", "deleted"]


@dataclass(frozen=True)
class ContextInfo:
    """公開されている 1 つの context（マウント）のメタ情報。"""

    name: str
    backend: str
    writable: bool = True


@dataclass(frozen=True)
class EntryInfo:
    """context 内の 1 エントリ（キー）。`key` は context 内の相対 posix パス。"""

    key: str
    size: int


@dataclass(frozen=True)
class Event:
    """ディレクトリ/ストアの変更通知。WS で push する 1 件。"""

    type: EventType
    context: str
    key: str
