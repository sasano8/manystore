# 挙動契約 — behavioral conformance spec

> 自動生成: `make conformance-docs`。手で編集しない。manystore のストア実装が満たすべき
> **挙動契約**の一覧。各契約は conformancer がテストとして実行し（① テスト可能）、
> pytest-cov に現れ（② 網羅可視）、この表に出力され（③ 仕様書）、
> 新 backend 実装の TODO になる（④ scaffold）。

## 絶対契約（オラクル非依存・全実装が満たす製品必須挙動）

`manystore.spec.conformancer` の各 assert 関数で検査する。新 backend は接続済みストアを
渡してこれらを呼べば、実装漏れが loud に落ちる。

| 契約ID | 内容 | 実装する検査 |
|---|---|---|
| `writer.all_or_nothing` | writer 内で例外が起きたら中途バッファを確定しない（キー不作成）。 | `assert_writer_aborts_on_error` |
| `put.create_only.concurrency` | 並行 create-only put は一方だけ成功・他方は ConflictError（二重作成なし）。 | `assert_put_if_absent_concurrency_safe` |
| `put.update_cas.concurrency` | 同一 base 版からの並行更新は一方だけ成功し lost-update を ConflictError で拒否。 | `assert_put_if_match_concurrency_safe` |
| `errors.fail_loud` | 下層障害を None/False/default/NotFound に化けさせず伝播（欠損のみ NotFound）。 | `assert_fail_loud_propagation` |
| `concurrent.overwrite_atomic` | if_match 無しの並行 put でも最終値はどちらか一方の完全値（torn/混在/空なし）。 | `assert_concurrent_overwrite_atomic` |
| `concurrent.delete_safe` | 並行 delete は冪等（障害のみ伝播）・並行 get は seed か NotFound・完了後は不在。 | `assert_concurrent_delete_safe` |
| `meta.sha256_correct` | 内容ハッシュを報告するなら実際の sha256 と一致（報告しない backend は免除）。 | `assert_head_sha256_correct` |

## 差分契約（辞書ストアをオラクルに観測一致を検査）

`FileStoreTester` が辞書ストア（正）と対象に同じ操作を適用し、返り値と適用後状態の一致を
観点ごとに検証する。下記の観点一覧は **run_* の実行から導出**（実態が正）。

### run_light

- `exists:missing`
- `open_reader:missing`
- `list_all:empty`
- `iter_all:empty`
- `open_writer:write`
- `exists:after_write`
- `list_all:after_write`
- `iter_all:after_write`
- `open_reader:full`
- `open_reader:partial`
- `open_writer:overwrite`
- `open_reader:after_overwrite`

### run_middle

- `delete:missing_idempotent`
- `write:a`
- `write:b`
- `write:c`
- `list_all:multi_key`
- `iter_all:multi_key`
- `open_reader:exact`
- `overwrite:shrink`
- `open_reader:after_shrink`
- `delete:b`
- `exists:after_delete`
- `list_all:after_delete`

### run_heavy

- `heavy:write_large`
- `heavy:read_large_full`
- `heavy:read_segments`
- `heavy:write_k00`
- `heavy:write_k01`
- `heavy:write_k02`
- `heavy:write_k03`
- `heavy:write_k04`
- `heavy:write_k05`
- `heavy:write_k06`
- `heavy:write_k07`
- `heavy:write_k08`
- `heavy:write_k09`
- `heavy:write_k10`
- `heavy:write_k11`
- `heavy:list_many`
- `heavy:iter_many`
- `heavy:overwrite_grow`
- `heavy:read_after_grow`
- `heavy:overwrite_shrink`
- `heavy:read_after_shrink`
- `heavy:overwrite_regrow`
- `heavy:read_after_regrow`

## 新しい backend の作り方（scaffold の出発点）

0. 雛形生成: `python -m manystore.spec.conformancer --scaffold MyStore --kind kv|file`
   ＝未実装メソッド（`raise NotImplementedError`）＋満たすべき契約 TODO＋配線手順が出る。
1. `KeyValueStore` / `FileStore` の Protocol メソッドを実装（`kv_spec.md` /
   `file_storage_spec.md` の ✅ を埋める）。`assert_key_value_store` 等で存在チェック。
2. 上記**絶対契約**の assert を接続済みストアに対して呼び、全て緑にする。
3. `FileStoreTester(DictFileStore(), <your_store>)` の `run_light`/`run_middle`/
   `run_heavy` を回し差分観点をオラクルに一致させる（run_* は非破壊）。
   `run_full` は差分（light+middle+heavy）＋絶対契約を 1 レポートに集約する一括実行。

