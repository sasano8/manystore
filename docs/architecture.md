# manystore アーキテクチャと設計原則（正本）

> このファイルが設計原則の **正本**。実行可能な正本は公開 Protocol（`manystore.spec`）と統合 facade
> `manystore.store`（M071＝1 つの Store）、準拠テスト（`manystore.spec.conformancer` /
> `tests/test_conformance.py`）。

## 1 つの Store（値 API ＋ IO API）

M071 で **公開は 1 つの Store**（`AsyncStore` / `SyncStore`）に畳んだ。1 つの Store が **値 API と IO API を
同じ面に載せる**:

```
Store の値 API : put / get / get_or_raise / iter_all / list_all / exists / delete / cp / mv / connect / aclose
Store の IO API: open_reader / open_writer（ストリーム IO）
```

`Store = 値 API(put/get …) + IO API(open_reader/open_writer)`。値はすべてバイナリ（`bytes`）。put/get だけ
見たい呼び出し側向けに、IO を除いた**部分集合の view** `AsyncBufferedStore`（put/get の 2 メソッド）も公開する
が、これは同じ 1 つの Store の狭い覗き窓であって別抽象ではない。

## get のセマンティクス

- primitive は **`get_or_raise(key) -> bytes`**：キーが無ければ `FileNotFoundError`（コードベース全体で欠損の正規例外）。
- **`get(key, default=None) -> bytes | None`** は基底 `BufferedStoreBase` が `get_or_raise` を捕捉して与える既定実装。
  backend は **`get_or_raise` だけ実装**すればよい（try/except を各所で重複させない）。

## 原則: 核（真実の実装）は native primitive 側に置く

1 つの Store は値 API と IO API の両方を持つが、backend の native primitive は片方に寄っていることが多い。
backend ごとに **値寄り / IO 寄り**を見極め、**逆向きに合成すると性能が落ちる方を native で実装**し、もう片方は
基底の既定合成に任せる（二重実装しない）。これを 3 つの基底クラスで表現する（`manystore.spec`）:

- **`BufferedStoreBase`（値寄り）** — native primitive が whole get/put。値 API を実装すれば、IO API
  （open_reader/open_writer）は基底が **whole の上に buffer 合成**（共有 `_KvReadFileObject` / `_KvWriteFileObject`）
  で既定提供する。例: NATS / HTTP / dict / remote。
- **`StreamingStoreBase`（IO 寄り）** — native primitive が stream IO（open/read/write）。IO API を実装すれば、
  値 API（get/put）は基底が open→read/write→close で既定合成する。例: Local。
- **`StreamableBufferedStoreBase`（両軸 native）** — 値 API も IO API も native で持てる backend 用。例: S3
  （whole get/put ＋ range body / multipart の native streaming）。※ whole を streaming から逆合成すると小さい値で
  multipart 過剰＝遅いので、両方 native に実装して取りこぼさない。

薄い側（基底が合成する側）に backend 固有ロジックを重複させない。片側固有操作（例 Local の `vacuum`）だけ足す。

### backend 別の核配置

| backend | 寄り | 基底クラス | もう片方の API の出自 |
|---|---|---|---|
| Local | IO 寄り | `StreamingStoreBase` | 値 API = 基底が IO から合成（native は filesystem の open/read/write） |
| S3 | 両軸 native | `StreamableBufferedStoreBase` | 値 API・IO API とも native（range body / multipart） |
| NATS | 値寄り | `BufferedStoreBase` | IO API = buffer 合成（真の streaming は nats-py 仕様で deferred） |
| HTTP | 値寄り（read-only） | `BufferedStoreBase` | IO API = buffer 合成（GET=whole）。write 系は `io.UnsupportedOperation` |
| dict（memory） | 値寄り | `BufferedStoreBase` | IO API = buffer 合成 |

> 旧 M071 以前は「KeyValueStore / FileStore」の 2 抽象と 2 方向アダプタ（`KeyValueFileStore` /
> `KeyValueFromFileStore`）で表現していたが、合成能力を基底へ内蔵したためアダプタは撤去した
> （どの backend も 1 つの full Store）。

## read-only の表現（既知の制約）

Protocol は read-only を静的に表せない。read-only backend（HTTP）は write 系メソッドが**型上は存在するが呼ぶと
`io.UnsupportedOperation`** を投げる（現状の方針＝YAGNI。capability 分割は需要が出てから別途）。

## 準拠の確認（conformance）

サードパーティ backend は `manystore.spec.conformancer` で検査できる。2 段階:

### ① メソッド存在チェック

`typing.get_protocol_members` が返す Protocol メンバが callable な属性として在るかを見る。

```python
from manystore.spec.conformancer import assert_buffered_store, assert_store
assert_buffered_store(MyStore())   # 値 API（put/get の view）が揃うか
assert_store(MyStore())            # full Store（値 API + IO API）が揃うか
```

### ② 挙動契約テスト（`StoreTester`・辞書ストアをオラクルに差分比較）

**辞書ストア（`DictStore`）を正（オラクル）**とし、同じ操作列を reference（辞書）と target の両方に適用して
**観測一致を観点ごとに**検証する。段階実行 `run_light` < `run_middle` < `run_heavy` < `run_full`（まず run_light）。

```python
import asyncio
from manystore import DictStore
from manystore.spec.conformancer import StoreTester, save_report

def test_my_store():
    tester = StoreTester(DictStore(), MyStore())               # 正=辞書, 対象
    report = []                                                # 呼び出し側がレポートを所有
    asyncio.run(tester.run_light(report))                      # 操作順に結果を追記
    assert all(s["passed"] for s in report)
    save_report(report, "my_store.conformance.json")           # 全保存
```

- run 系（`run_light` 等）は**レポート（list）を受け取り操作順に観測結果を追記**する。**ツールはレポートを保持
  しない**（呼び出し側が所有・`save_report` で保存）。
- **run_light** = open_reader / open_writer / exists / **list_all** / **iter_all**（＋欠損）を 12 観点で差分検証。
  `list_all` は **`iter_all` を呼ぶ**形（materialize）。どちらも**全キーを平坦に**列挙する（'/' ネストも再帰的に。
  1 階層だけを返す概念は持たない＝KVS はフラット。`limit` は安全上限）。
- **delete_all** はクリーンな初期状態を作る基盤操作（ジェネシス＝検証困難なので run_light の対象外・使うだけ）。
- 各エントリは `op` / `args` / `expected` / `actual` / `passed` を持ち、**将来リプレイ**（保存結果を別実装へ
  再適用）に使える JSON 構造。
- `spec`（値寄り / IO 寄り 等）の出力で**特性表**をまとめる。**自動検出は別タスク（M022b）**。

実 backend（local/nats/s3）の KVS ラウンドトリップは `tests/test_e2e_backends.py`（`make e2e-up`）。
**run_middle/heavy/full・シグネチャ検査・spec 自動検出は未実装**（M022 P3）。
