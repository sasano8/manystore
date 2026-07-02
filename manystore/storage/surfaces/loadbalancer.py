"""load balancer — 複数の交換可能な backend から **負荷に応じて1つを選んで**操作する合成ストア。

> ⚠️ ここにあるのは「あるべき場所に置いた空定義 ＋ アルゴリズムのネタ」であり、メソッド本体は
> `NotImplementedError`。本実装は別タスクで詰める。**facade（kv.py）には未公開**（未完成のため）。

## [ArrayStore] との関係（兄弟であって素直な派生ではない）
共通点は「複数 [KeyValueStore] ＋ ルーティング ＋ 横断列挙」。だが [ArrayStore] とは 2 点で
本質的に違う:

1. **行き先が鍵に出ない**。Array は鍵 `<mount>/<sub>` に行き先が書いてある（決定的・明示）。LB は
   **member を匿名のリストで持ち**、鍵に member を書かない（どれに入っていてもよいのが主旨）。
   よって `iter_all` は Array のように名前 prefix しない（生の横断）。
2. **ルーティングが「負荷」で決まる**。Array は鍵→mount の決定的写像。LB は backend が報告する負荷
   メトリクス（CPU/メモリ/空き容量…）を見て[BalancePolicy]がその時点の最適 member を選ぶ。

そのため [ArrayStore] を継承して `_route` だけ差し替える、は罠（iter/exists/cp/mv も総取り
換え）。**[BufferedStoreBase] を継承した兄弟**として置く（共通 composite 基底の抽出は将来 YAGNI）。

## 未解決の論点（読みのルーティング）— scaffold では probe-all を既定とする
「書きは負荷で1つ選ぶ」は決まるが、**後でその鍵を read/delete するとき、どの member に入れたかが
分からない**（負荷で毎回選ぶと読みが当たらない）。最小の既定は **probe-all**＝各 member を順に試す
（配置インデックス不要・member は匿名/差し替え可を保てる）。将来は key→member の配置インデックスを
持つ案もある（速いが状態が増える）。本実装時にどちらかへ倒す（追跡は backlog M040）。
"""

from collections.abc import AsyncIterator
from typing import Protocol, TypedDict, runtime_checkable

from ...spec import (
    AsyncBufferedStore,
    BufferedStoreBase,
    FileInfo,
    IfMatch,
    _aclose_all,
    _connect_all,
)


# ── ネタ①: 負荷メトリクスの capability（backend が任意で報告する） ──
class LoadStats(TypedDict, total=False):
    """backend が報告する負荷・容量メトリクス（[BalancePolicy] の入力）。

    全項目 optional（`total=False`）＝報告できるものだけ埋める。例: local は `shutil.disk_usage` で
    free/total を埋められるが cpu/mem は別途エージェントが要る。S3 は free 概念が無い（埋めない）。
    """

    cpu_percent: float  # CPU 使用率 0..100
    mem_percent: float  # メモリ使用率 0..100
    free_bytes: int  # 書き込み可能な空き容量
    total_bytes: int  # 総容量
    score: float  # backend 自身が出す合成スコア（小さいほど空いている、等）


@runtime_checkable
class SupportsLoadStats(Protocol):
    """負荷メトリクスをネイティブに報告できる backend の **optional capability**。

    core IF（[KeyValueStore]）には載せない（最小・汎用）。報告できる backend だけが実装し、
    [LoadBalancedStore] は capability を持つ member からのみ [LoadStats] を集める
    （持たない member は「不明」として policy が扱う）。
    """

    async def load_stats(self) -> LoadStats: ...


# ── ネタ②: 選択アルゴリズム（どの member を選ぶか） ──
class BalancePolicy(Protocol):
    """member 群＋各 member の [LoadStats]（不明は None）から 1 つを選ぶ戦略。

    返すのは選んだ member の index。stats を使わない戦略（round_robin）も同じ IF に乗る。
    """

    def select(self, members: list[AsyncBufferedStore], stats: list[LoadStats | None]) -> int: ...


class RoundRobinPolicy:
    """メトリクス非依存で順番に選ぶ（最も単純な基準・複製運用や均一 backend 向け）。"""

    def __init__(self) -> None:
        self._next = 0

    def select(self, members: list[AsyncBufferedStore], stats: list[LoadStats | None]) -> int:
        raise NotImplementedError("loadbalancer scaffold: RoundRobinPolicy.select")


class MostFreeSpacePolicy:
    """`free_bytes` が最大の member を選ぶ（空き容量ベースの配置）。"""

    def select(self, members: list[AsyncBufferedStore], stats: list[LoadStats | None]) -> int:
        raise NotImplementedError("loadbalancer scaffold: MostFreeSpacePolicy.select")


class LeastLoadedPolicy:
    """cpu/mem を重み付け合成した負荷が最小の member を選ぶ（負荷ベースの配置）。

    重みは init で受ける（例 cpu:mem = 0.7:0.3）。stats 不明の member の扱い（除外/最劣後）も
    本実装で決める。
    """

    def __init__(self, cpu_weight: float = 0.7, mem_weight: float = 0.3) -> None:
        self._cpu_weight = cpu_weight
        self._mem_weight = mem_weight

    def select(self, members: list[AsyncBufferedStore], stats: list[LoadStats | None]) -> int:
        raise NotImplementedError("loadbalancer scaffold: LeastLoadedPolicy.select")


class LoadBalancedStore(BufferedStoreBase):
    """匿名の member 群を負荷で選んで束ねる合成 [KeyValueStore]（[ArrayStore] の兄弟）。

    本体は未実装（`NotImplementedError`）。書きは [BalancePolicy] で 1 member を選び、読み系は
    probe-all（既定）。詳細はモジュール docstring 参照。
    """

    def __init__(self, policy: BalancePolicy | None = None) -> None:
        self._members: list[AsyncBufferedStore] = []
        self._policy = policy or RoundRobinPolicy()

    def add_member(self, store: AsyncBufferedStore) -> None:
        """負荷分散対象の backend を1つ追加する（匿名・順序のみ意味を持つ）。"""
        self._members.append(store)

    def members(self) -> list[AsyncBufferedStore]:
        return list(self._members)

    async def _collect_stats(self) -> list[LoadStats | None]:
        """各 member の [LoadStats] を集める（[SupportsLoadStats] 非対応は None）。"""
        raise NotImplementedError("loadbalancer scaffold: _collect_stats")

    async def _select(self) -> AsyncBufferedStore:
        """policy で書き込み先 member を1つ選ぶ。"""
        raise NotImplementedError("loadbalancer scaffold: _select")

    async def put(self, key: str, value: bytes, *, if_match: IfMatch = None) -> FileInfo:
        # 負荷で1 member を選んで書く（選んだ先は鍵に残さない＝読みは probe-all）。
        raise NotImplementedError("loadbalancer scaffold: put")

    async def get_or_raise(self, key: str) -> bytes:
        # probe-all: 各 member を順に get、最初に見つかった値を返す（全滅は FileNotFoundError）。
        raise NotImplementedError("loadbalancer scaffold: get_or_raise")

    async def iter_all(self, limit: int | None = None, prefix: str = "") -> AsyncIterator[FileInfo]:
        # 全 member を横断（鍵は member 名で前置しない）。prefix は各 member の iter_all へ委譲。
        # TODO(M040): dedup 方針は本実装で決定（同一鍵が複数 member にある場合）。
        raise NotImplementedError("loadbalancer scaffold: iter_all")
        yield  # 未到達（async generator 化のため）

    async def exists(self, key: str) -> bool:
        raise NotImplementedError("loadbalancer scaffold: exists")  # probe-all

    async def delete(self, key: str) -> None:
        raise NotImplementedError("loadbalancer scaffold: delete")  # probe-all（全 member から）

    async def cp(self, src: str, dst: str) -> None:
        raise NotImplementedError("loadbalancer scaffold: cp")

    async def mv(self, src: str, dst: str) -> None:
        raise NotImplementedError("loadbalancer scaffold: mv")

    async def connect(self) -> None:
        await _connect_all(self._members)  # 途中失敗で確立済みを巻き戻す（M057）

    async def aclose(self) -> None:
        await _aclose_all(self._members)  # 全件閉じ切る（1 つの失敗で残りを漏らさない・M057）
