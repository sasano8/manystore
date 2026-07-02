# 新しい backend を実装する（ガイドライン）

manystore の **conformance（挙動契約）を仕様の単一源泉**とし、新しい backend はそれに沿って実装して
「契約を通す」だけで完成に近づく（北極星）。本ページはその道しるべ。

## 全体像（5 ステップ）

1. **契約を読む** — `manystore/protocols.py`（`AsyncKeyValueStore` / `AsyncFileStore` の Protocol＋既定実装）と
   設計原則の正本 [`docs/architecture.md`](architecture.md)。挙動契約の一覧は
   [Conformance spec](conformance_spec.md)。
2. **雛形を起こす** — `python -m manystore.tools.conformancer --scaffold MyStore --kind kv|file` が
   実装 TODO 付きの skeleton を出力する（契約が実装の TODO リストになる）。
3. **実装する** — 参照実装 `DictKeyValueStore`（`backends/memory.py`＝in-memory・依存ゼロ）を手本に。
   核（真の実装）は native primitive 側に置く（kv 寄り/file 寄りの見極めは architecture.md）。
4. **登録する** — [registry](backend_registry.md) に `register_backend("mybackend", kv_factory=…)`
   （同梱なら builtin seed／プラグインは entry-point group `manystore.stores`）。
5. **契約を通す** — conformance matrix に provider を 1 行足し、`assert_*` / `run_light·middle·heavy·full`
   を流す。緑になれば「他 backend と同じ観測契約を満たす」ことが機械的に保証される。

## 参照実装とオラクル

- **`DictKeyValueStore`＝参照 backend**。Protocol を最小・素直に実装した in-memory 版で、conformance の
  **オラクル**（run_light/middle/heavy が「辞書ストアと同じ観測になるか」で判定）。「どう実装すべきか」に
  迷ったらまずこれを読む。
- 逆向き合成の実例＝`KeyValueFileStore`（KVS→FileStore）/ `KeyValueFromFileStore`（FileStore→KVS）。
  put/get しか native に無くても FileStore を、open しか無くても KVS を、合成で満たせる（原則6）。

## テストの 3 レイヤ（real / fake / fault）と**権威の所在**

同じ契約を 3 つの「駆動源」で流せる。**それぞれ守備範囲が違う**ので、何を証明できるかを取り違えない。

| 駆動源 | 何を回すか | 何を証明するか | 何を**証明しないか** |
|--------|-----------|---------------|---------------------|
| **real**（gated・docker） | 実 backend（nats/s3…） | **意味論の認証**（並行原子性・CAS・耐久性） | — |
| **fake**（`tests/fakes.py`・非 gated） | adapter は本物・低層 client だけ in-memory fake | **コードパス網羅・fault の器**（docker 無し fast） | **並行/CAS の意味論**（単一プロセス in-memory＝競合が起きない） |
| **fault**（注入） | 下層を故障プロキシに差し替え | **fail-loud**（障害を欠損/False/default に化けさせない） | — |

- **fake は "こういうケースがあり得る" を安く網羅・文書化する層／real は "実際に正しい" を認証する層**。
  fake が緑でも並行/CAS の正しさは保証しない（例＝排他ロック・条件付き put は fake では非権威＝
  `unsupported` で **xfail 非strict**）。認証は **real（gated）＋決定的 white-box テスト**（例:
  `test_local_delete_idempotent_under_toctou`＝TOCTOU を monkeypatch で確定再現）に残す。
- fake が忠実であるべきは**観測契約**（CRUD・メタ round-trip・fail-loud）。そこが実装とズレたら、
  **同じ契約を real にも流している**ので CI e2e が炙り出す（fake の忠実性が契約で守られる）。
- fake の作り方は `tests/fakes.py`（低層トランスポート模型）。差し替えは backend の接続点だけ
  （S3=`_session`／NATS=`_get_obs`）で、adapter コード自体は本物を走らせる。

## conformance matrix に足す（registry 駆動・profile 宣言）

**構築は registry に委ねる**ので、テスト側に construct を書かない。`tests/conformance_providers.py` の
`_gated_profiles()` に **`BackendProfile` を 1 つ**足すだけ（M077）:

```python
BackendProfile(
    "mybackend", "mybackend",          # id, registry 名
    opts={"...": "..."},               # 接続 opts（registry factory へ渡る flat kwargs）
    gated=True, reachable=_mybackend_up,
    unsupported=frozenset({...}),      # 保証しない契約キー＝xfail 明示
    setup=_mybackend_setup,            # 任意: 接続前の準備（例 s3 の bucket 作成）
)
```

`_profile_opener` が **registry（`get_backend_spec`）で store を構築 → connect → cleanup** を一元化する
（`_build_filestore` が `file_factory`／KVS を wrap）。ベタな construct/connect は書かない。

- `gated=True` … 実 backend（未到達なら skip・`slow`）。docker 無しで回したいなら **fake provider**
  （低層 client を fake に差し替え・非 gated）を併せて足す。
- `unsupported` … 保証しない契約キー（能力差・fake の非権威）を宣言＝暗黙 skip でなく**明示の xfail 行**。
- **per-open リソースが要る**もの（tmp dir・in-process サーバ・fault 注入）だけ custom opener を書く
  （registry はテスト環境の結線を知らない＝そこだけ手当て）。

これで新 backend は「**registry 登録（M068）＋ profile 1 行**」で matrix に参加し、契約一覧
（＝実装の TODO）を潰して緑にすれば横断的に準拠が保証される。
