"""connection lifecycle — 接続前の状態（factory 包み）を async with で接続して使う。

インスタンス初期化では接続せず、`async with` で初めて接続する。Local も接続不要だが
ステップを合わせるため connect/aclose を持つ。

- [ConnectPolicy] … 接続の方針（初回 timeout・リトライ・バックオフ・全体 deadline）。
- [connecting] … 任意のストア factory を包む汎用の async context manager。
- [connect_key_value_store] … backend 名から接続前状態を作る入口。

`verify` と `ConnectPolicy` は役割が別:
- `ConnectPolicy` … 接続を**何回・どれだけ粘って**試すか（初回 timeout・リトライ・deadline）。
- `verify=True` … 粘っても駄目なら最終的に**送出**（fail-fast）。`verify=False` … 失敗を**無視**して
  先へ進む（以降の lazy 接続・再試行に委ねる）。
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .storage.backends import create_key_value_store
from .protocols import AsyncKeyValueStore


@dataclass(frozen=True)
class ConnectPolicy:
    """接続の方針（初回試行・リトライ・timeout・deadline をまとめて司る）。

    既定値（max_retry 以外は有限＝無限待機しない）:
        max_retry=inf, timeout=10.0, delay=0.5, backoff=2.0, max_delay=30.0, deadline=60.0

    max_retry: リトライ回数。`float("inf")` で無制限（終了は deadline 任せ）。0 でリトライ無し。
    timeout: 1 回の connect の最大待機秒（None で per-attempt 無制限）。超過は失敗扱いで次へ。
    delay: リトライ間の待機秒の初期値。
    backoff: リトライ毎に delay へ掛ける係数（指数バックオフ。1.0 で固定間隔）。
    max_delay: delay の上限（None なら無制限）。
    deadline: 全試行を通じた最大待機秒（None で無制限）。1 回の待機にも効く。

    1 回の待機は `timeout` と「残り deadline」の小さい方で縛る。**`max_retry` を inf にするなら
    `deadline` を有限に**しないと止まらない（既定は deadline=60 で頭打ち）。
    """

    max_retry: float = float("inf")
    timeout: float | None = 10.0
    delay: float = 0.5
    backoff: float = 2.0
    max_delay: float | None = 30.0
    deadline: float | None = 60.0

    @classmethod
    def default(cls) -> ConnectPolicy:
        """既定。失敗しても指数バックオフで deadline=60s まで粘る（バランス型）。"""
        return cls()

    @classmethod
    def fail_fast(cls) -> ConnectPolicy:
        """即決。リトライせず短い timeout で 1 回だけ試す（到達性を素早く判定したいとき）。"""
        return cls(max_retry=0, timeout=5.0, deadline=5.0)

    @classmethod
    def forever(cls) -> ConnectPolicy:
        """無期限に粘る。依存サービスが起動するまで deadline 無しでリトライし続ける。

        1 回の connect は timeout=10s で縛るので、単発のハングは弾いて次の試行へ回す
        （成功するまで永遠に待つが、CPU を焼かないよう delay/backoff を入れている）。
        """
        return cls(
            max_retry=float("inf"),
            timeout=10.0,
            delay=1.0,
            backoff=2.0,
            max_delay=30.0,
            deadline=None,
        )


async def _connect_with_retry(store: AsyncKeyValueStore, policy: ConnectPolicy) -> None:
    """`store.connect()` を policy に従って試す。最終的に失敗したら最後の例外を送出。

    1 回の待機は `timeout` と「残り deadline」の小さい方で縛る。max_retry が inf（無制限）の
    ときは attempt 数で打ち切らず、deadline 到達で止める。
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    delay = policy.delay
    attempt = 0
    last_exc: BaseException | None = None
    while True:
        # 残り deadline と per-attempt timeout の小さい方で 1 回の待機を縛る。
        eff = policy.timeout
        if policy.deadline is not None:
            remaining = policy.deadline - (loop.time() - start)
            if remaining <= 0:
                break  # deadline 使い切り
            eff = remaining if eff is None else min(eff, remaining)

        attempt += 1
        try:
            if eff is not None:
                await asyncio.wait_for(store.connect(), eff)
            else:
                await store.connect()  # timeout/deadline とも None＝明示的に無制限
            return
        except Exception as exc:  # timeout（TimeoutError）含む
            last_exc = exc

        if attempt >= policy.max_retry + 1:  # 総試行 = max_retry + 1（inf なら打ち切らない）
            break
        if policy.deadline is not None:
            remaining = policy.deadline - (loop.time() - start)
            if remaining <= 0:
                break
            sleep_for = min(delay, remaining) if delay > 0 else 0
        else:
            sleep_for = delay
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        delay *= policy.backoff
        if policy.max_delay is not None:
            delay = min(delay, policy.max_delay)

    if last_exc is None:
        # deadline を即使い切り、1 度も試行できなかった等。
        raise TimeoutError("connect deadline exceeded before any attempt")
    raise last_exc


@asynccontextmanager
async def connecting(
    factory: Callable[[], AsyncKeyValueStore],
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
) -> AsyncIterator[AsyncKeyValueStore]:
    """`factory()` で実体を生成し（policy に従い）connect してから yield、終了時に aclose する。

    実体生成と接続を `__aenter__` まで遅延する（factory で包んだ状態＝まだ接続していない）。
    """
    pol = policy or ConnectPolicy()
    store = factory()
    try:
        await _connect_with_retry(store, pol)
    except Exception:
        if verify:
            await store.aclose()
            raise
        # verify=False: 粘っても駄目なら無視して先へ進む。
    try:
        yield store
    finally:
        await store.aclose()


def connect_key_value_store(
    backend: str,
    *,
    verify: bool = True,
    policy: ConnectPolicy | None = None,
    **opts: object,
):
    """backend を接続前の状態（factory 包み）として返す。`async with ... as store` で接続して使う。

    例: `async with connect_key_value_store("nats", url=u, bucket=b,
            policy=ConnectPolicy(timeout=2.0, deadline=30.0)) as store: ...`
    """
    return connecting(
        lambda: create_key_value_store(backend, **opts),
        verify=verify,
        policy=policy,
    )
