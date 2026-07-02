"""mirror — source → sink の **片方向同期（one-way mirror）**。

2 つの [KeyValueStore] を扱い、**source を常に正**として sink を完全同期する。方式は
**集合差 reconcile**（両側を `iter_all` で列挙し、ファイルの存在有無を突き合わせる）:

- source にあり sink に無い … **create**（新規コピー）
- 両方にあり comparator が「異なる」と判定 … **update**（source で上書き）
- 両方にあり comparator が「同じ」 … **skip**（無駄な更新を省く）
- sink にあり source に無い … **delete（prune）**。事故防止で opt-in（既定 `prune=False`＝keep）。

不変条件（= one-way）: 書き込みは source→sink の 1 方向のみ。sink→source の書き戻しはしない
（最小宣言＝source を補完で汚さない）。

comparator は `(src_info, sink_info) -> 更新が要るか`。既定は **size 比較**（[FileInfo] は
filename/size のみ＝mtime を持たない。更新日時での比較は M013 メタ拡張待ち。厳密一致が要るなら
内容比較の comparator を渡す）。
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from ...protocols import AsyncBufferedStore, FileInfo

# (source 側 info, sink 側 info) を受け取り「更新が必要か」を返す述語。
Comparator = Callable[[FileInfo, FileInfo], bool]


def size_differs(src: FileInfo, sink: FileInfo) -> bool:
    """既定 comparator＝**サイズが違えば更新**（同じサイズは skip）。

    [FileInfo] は filename/size しか持たないため size のみで判定する（安価だが「同サイズで内容だけ
    変化」は取り逃す）。更新日時での比較は M013（メタデータ）待ち。厳密さが要るなら内容ハッシュ等の
    comparator を [StorageMirror] に渡す。
    """
    return src["size"] != sink["size"]


@dataclass
class SyncPlan:
    """同期前に算出する計画（実行はしない＝dry-run に使える）。各リストはキー名（昇順）。"""

    create: list[str] = field(default_factory=list)  # source のみ＝新規コピー
    update: list[str] = field(default_factory=list)  # 両方あり・comparator が「異なる」
    delete: list[str] = field(
        default_factory=list
    )  # sink のみ＝prune 対象（prune=False なら未使用）
    skip: list[str] = field(default_factory=list)  # 両方あり・comparator が「同じ」


@dataclass
class SyncResult:
    """同期の実行結果（実際に適用したキー名・昇順）。"""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class StorageMirror:
    """2 つの [KeyValueStore]（source / sink）を扱い、source → sink を片方向同期する。

    `source` を正とし、`sink` を source に一致させる。`compare` で無駄な更新をスキップする
    （既定 [size_differs]）。`plan()` で計画だけ算出（dry-run）、`sync()` で適用する。
    """

    def __init__(
        self,
        source: AsyncBufferedStore,
        sink: AsyncBufferedStore,
        *,
        compare: Comparator = size_differs,
    ) -> None:
        self._source = source
        self._sink = sink
        self._compare = compare

    async def _index(self, store: AsyncBufferedStore) -> dict[str, FileInfo]:
        """`store` の全キー → [FileInfo] の索引（存在有無＋size の突合に使う）。"""
        return {info["filename"]: info async for info in store.iter_all()}

    async def plan(self, *, prune: bool = False) -> SyncPlan:
        """同期計画を算出する（適用なし）。`prune=True` で sink 余剰キーを delete 対象に含める。"""
        src = await self._index(self._source)
        dst = await self._index(self._sink)
        plan = SyncPlan()
        for key, sinfo in src.items():
            if key not in dst:
                plan.create.append(key)
            elif self._compare(sinfo, dst[key]):
                plan.update.append(key)
            else:
                plan.skip.append(key)
        if prune:
            plan.delete = sorted(k for k in dst if k not in src)
        plan.create.sort()
        plan.update.sort()
        plan.skip.sort()
        return plan

    async def sync(self, *, prune: bool = False) -> SyncResult:
        """source → sink を同期する。`prune=True` のとき source に無い sink のキーを削除する。

        create/update はいずれも **source の値で put**（source が正＝上書き）。計画段階で create と
        update を分けるのは観測（結果レポート）のためで、書き込み自体はどちらも put。
        """
        plan = await self.plan(prune=prune)
        for key in plan.create + plan.update:
            await self._sink.put(key, await self._source.get_or_raise(key))
        for key in plan.delete:
            await self._sink.delete(key)
        return SyncResult(
            created=plan.create, updated=plan.update, deleted=plan.delete, skipped=plan.skip
        )
