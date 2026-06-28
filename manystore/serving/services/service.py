"""service — protocol を manystore の [KeyValueStore] へ写す中核（[StorageService]）。

HTTP の **context（第一階層）は [ArrayKeyValueStore] の mount に対応**する。config の各 context を
`create_unsafe_key_value_store` で生成 → [SafeKeyValueStore]（キー検証）で包み ArrayStorage に
`mount` し、(context, key) を `<context>/<key>` キーへ合成して 1 本の合成ストアへ写す＝**振り分けは
ArrayStorage に委譲**（service は writable・メタ・watcher だけを上載せ）。一覧は ArrayStorage の
`iter_all()`（各 mount を `<name>/` 前置する横断列挙）を context で切り出し prefix 絞り。各 context
に [PollingWatcher] を 1 本張り WS 購読へ fan-out する。

HTTP には一切依存しない＝この層だけで単体テストできる。
"""

import contextlib

from ...exceptions import ContextNotFound, ReadOnlyContext  # 集約先（後方互換で再エクスポート）
from ...protocols import DEFAULT_LIST_LIMIT, FileInfo, IfMatch
from ...storage.backends import create_unsafe_key_value_store
from ...storage.surfaces.array import ArrayKeyValueStore
from ...storage.surfaces.safe import SafeKeyValueStore
from .config import AppConfig
from .protocol import ContextInfo, EntryInfo
from .watcher import PollingWatcher

__all__ = ["StorageService", "ContextNotFound", "ReadOnlyContext"]


class StorageService:
    """公開 context 群を保持し、protocol の操作を KeyValueStore に写すアプリ中核。"""

    def __init__(self, config: AppConfig, *, watch_interval: float = 1.0) -> None:
        self._config = config
        self._watch_interval = watch_interval
        # context = 第一階層の合成ストア。振り分け・横断列挙・跨ぎ cp/mv は ArrayStorage に委譲。
        self._array = ArrayKeyValueStore()
        self._watchers: dict[str, PollingWatcher] = {}

    # ── ライフサイクル ──

    async def connect(self) -> None:
        """全 context のストアを生成して ArrayStorage に mount し、ウォッチャを起動する。

        途中の context で失敗したら、それまでに確立した mount/watcher を巻き戻してから再送出する
        （部分起動でストア・watcher task をリークさせない・M057）。
        """
        try:
            for name, cc in self._config.contexts.items():
                raw = create_unsafe_key_value_store(cc.backend, **cc.opts)  # type: ignore[arg-type]
                store = SafeKeyValueStore(raw)  # キー検証は mount したストア側で効く
                await store.connect()  # mount は登録のみ＝接続は明示的に行う（責務分離）
                await self._array.mount(
                    name, store
                )  # 第一階層へ登録（I/O なし。mount は非同期 IF）
                watcher = PollingWatcher(store, name, interval=self._watch_interval)
                self._watchers[name] = (
                    watcher  # 記録してから start（start 失敗でも aclose が掴める）
                )
                await watcher.start()
        except Exception:
            with contextlib.suppress(Exception):
                await self.aclose()  # 部分確立分（mount 済みストア・起動済み watcher）を巻き戻す
            raise

    async def aclose(self) -> None:
        """ウォッチャを止め、ArrayStorage（＝全 mount）を閉じる。

        watcher の停止と array の close は**全て試し**、1 つの失敗で残りを閉じ漏らさない（M057）。
        """
        errors: list[Exception] = []
        for watcher in self._watchers.values():
            try:
                await watcher.aclose()
            except Exception as e:  # noqa: BLE001  全 watcher を止め切ってから最初の例外を送出
                errors.append(e)
        self._watchers.clear()
        try:
            await self._array.aclose()  # mount は _aclose_all で全件閉じる
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        if errors:
            raise errors[0]

    # ── 参照 ──

    def _require_context(self, context: str) -> None:
        """未公開の context を弾く（ArrayStorage の mount 表を正とする）。"""
        if context not in self._array.mounts():
            raise ContextNotFound(context)

    def _key(self, context: str, key: str) -> str:
        """HTTP の (context, key) を ArrayStorage の `<context>/<key>` キーへ合成する。"""
        self._require_context(context)
        return f"{context}/{key}"

    def list_contexts(self) -> list[ContextInfo]:
        return [
            ContextInfo(name=cc.name, backend=cc.backend, writable=cc.writable)
            for cc in self._config.contexts.values()
        ]

    def featured(self) -> list[dict[str, object]]:
        """ビュー重点設定（views.featured）を素の dict 列で返す（protocol の一部）。"""
        return [
            {
                "context": fv.context,
                "path": fv.path,
                "label": fv.label,
                "pin": fv.pin,
                "quick_write": fv.quick_write,
            }
            for fv in self._config.featured
        ]

    @property
    def default_context(self) -> str:
        return self._config.default_context

    def watcher(self, context: str) -> PollingWatcher:
        try:
            return self._watchers[context]
        except KeyError:
            raise ContextNotFound(context) from None

    # ── CRUD ──

    async def list_entries(
        self, context: str, prefix: str = "", limit: int = DEFAULT_LIST_LIMIT
    ) -> list[EntryInfo]:
        """context 内を prefix 絞りで返す。

        prefix フィルタは **`iter_all(prefix=…)`** に委譲＝S3 はサーバ側 `list_objects_v2(Prefix=…)`
        で native に絞り、native を持たない backend は scan+filter で支える。ArrayStorage が
        第一セグメント（=context）で mount を絞るので、別 context は走査しない。`<context>/` を
        剥がして bucket 相対キーへ戻す。
        """
        self._require_context(context)
        scope = f"{context}/"
        out: list[EntryInfo] = []
        async for info in self._array.iter_all(prefix=scope + prefix):
            key = info["filename"][len(scope) :]
            out.append(EntryInfo(key=key, size=info["size"]))
            if len(out) >= limit:
                break
        return out

    async def get_or_raise(self, context: str, key: str) -> bytes:
        """欠損なら `FileNotFoundError`（get の primitive・client/backend と同じ規約）。"""
        return await self._array.get_or_raise(self._key(context, key))

    async def get(self, context: str, key: str, default: bytes | None = None) -> bytes | None:
        return await self._array.get(self._key(context, key), default)

    async def exists(self, context: str, key: str) -> bool:
        return await self._array.exists(self._key(context, key))

    async def head(self, context: str, key: str) -> FileInfo:
        """メタ情報（size/modified_at/etag）を返す。欠損は `NotFoundError`（CAS の version 読口）。

        filename は外向きの bare key に直す（合成キー `<context>/<key>` は隠す）。etag は下層の
        不透明トークンを透過＝client がそのまま `put(if_match=...)` 相当の条件ヘッダに使える。
        """
        info = await self._array.head(self._key(context, key))
        return FileInfo(
            filename=key,
            size=info.get("size"),
            modified_at=info.get("modified_at"),
            etag=info.get("etag"),
        )

    async def head_or_absent(self, context: str, key: str) -> FileInfo:
        """`head`（存在）か 不在 [FileInfo]（size=None）を返す＝HEAD 応答の 200/404 判定に使う。"""
        try:
            return await self.head(context, key)
        except FileNotFoundError:
            return FileInfo.absent(key)

    async def put(
        self, context: str, key: str, value: bytes, *, if_match: IfMatch = None
    ) -> FileInfo:
        """値を書く。`if_match` で conditional put（None=LWW／不在=create-only／FileInfo=CAS）。

        条件不一致は backend が `ConflictError` を上げ、route が problem(409) に写す（fail-loud）。
        """
        self._require_writable(context)
        return await self._array.put(self._key(context, key), value, if_match=if_match)

    async def delete(self, context: str, key: str) -> None:
        self._require_writable(context)
        await self._array.delete(self._key(context, key))

    def _require_writable(self, context: str) -> None:
        cc = self._config.contexts.get(context)
        if cc is None:
            raise ContextNotFound(context)
        if not cc.writable:
            raise ReadOnlyContext(context)
