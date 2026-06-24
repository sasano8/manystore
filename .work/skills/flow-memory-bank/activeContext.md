# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）に
> 畳む（2026-06-24 memory clean 実施＝旧フォーカス 13 ブロックを progress へ集約・削除）。

## 現在のフォーカス

**protocols.py を「契約＋既定実装の唯一の源泉」に確定（2026-06-25 完了）。`stores/base.py` を削除し、
既定実装（`FileStoreBase`/`KeyValueStoreBase`・アダプタ `KeyValueFileStore`/`KeyValueFromFileStore`・
共有ヘルパ `iter_prefix`/`scan_prefix`/`_kv_copy`/`_kv_move`/`_atomic_write_bytes`/`_Kv*FileObject`）を
protocols.py に集約。全 import を `..protocols` へ向け替え。`sync_storage.py` も削除済。fast 113 green・format green。**

- **完了・コミット済**: ① protocols.py に契約集約＋各 import を `..protocols` へ ② `FileStoreBase` 新設＋
  `LocalFileStore(FileStoreBase)`（file 寄り＝primitive=open_reader/writer・KVS 面は IO から導出）③ **`iter_all`
  を全 async ストアで `async def`（async ジェネレータ）に統一**（コルーチン化バグを是正）④ **`iter_all`/`list_all`
  が `limit: int | None = None`**（limit は iter_all に一元適用・list は materialize）＝`_take` ヘルパ削除。
- **2026-06-25 で完了**: 上記「残」は解消。`stores/base.py` を削除し中身を protocols.py に全面集約、
  `KeyValueFileStore` を `file.py` の公開 API に追加（フラット再エクスポート）。`make test` 失敗の原因は
  **test_storage.py が Protocol の `AsyncKeyValueStore` を adapter として instantiate していた**こと＝
  正しい `KeyValueFileStore` に修正。protocols.py/local.py に潜在していた E501 ドリフト（base.py 由来）も是正。
  ※`from __future__ import annotations` は [3.14 前提で全廃] 方針に従い**入れない**（PEP 649 で前方参照は valid）。

- ユーザーが IDE で実装ファイルを移動・改名済＝`manystore/stores/{base,array,safe,sync_bridge}.py`
  （旧 async_storage/array_storage/safe_path/async_to_sync_storage）＋ `conformance.py`→`conformancer/`。
  `sync_storage.py` は root 残置（純 Protocol）。**`protocols.py` への Protocol 抽出はせず**（ユーザー判断＝
  base.py が Protocol＋実装を保持）。ツリー green（fast 113 passed）。
- **ドキュメント参照の整理（このタスク・ユーザー方針）**: 「ドキュメントに書くと不整合が生じる＝必要以上の参照は
  持たず消す（本当に必要ならリネームして残す）」。systemPatterns のファイル別インベントリを概念レベルへ畳み
  （ファイル名列挙をやめ facade `kv`/`file`＋`docs/architecture.md` を正本に）、コード docstring の旧モジュール参照
  （`[async_storage]`/`safe_path` 等）を削除/改名、`docs/architecture.md` の Protocol 参照を公開シンボルへ。完了 plan
  `m035-impl-protocol-split-plan.md` は役目を終えたので削除。

## 直近の変更

- **memory clean 実施（2026-06-24・supervisor 高優先指示）**: activeContext が 559 行/61KB・「（旧フォーカス）」
  13 ブロックに肥大→**今の焦点スナップショットへ圧縮**（履歴は progress.md M-row に在るため削除）。指示は
  `interrupt/archive/2026-06-24-memory-clean-activecontext.md`。※この指示は funnel をすり抜け一時 git から消えかけたが
  `ef5b645` 履歴から復元・処理済み。
- **interrupt `aa.md` 取り込み（2026-06-24）**: 要求1（e2e タイムアウト→即 skip）→**M037**（下記 supervisor 指示に内包）／
  要求2（`m0xx-*-plan.md` の置き場所を相談）→**下記「進行中の決定」で要相談**／要求3（sync/async プロトコル共通管理）→
  **M035 の protocols.py 集約に吸収**。`aa.md` は archive へ。
- **supervisor 高優先指示 取り込み（2026-06-24・テスト軽重分離 R13）→ M037**: `@pytest.mark.slow` 軽重分離＋
  `make test`(fast)/`make test-all` 分離＋s3-virtual の早期 skip（timeout 待ち撤廃）。**このサイクルで実装**。次フォーカス。
- **このセッションの実装（agent ブランチ）**: M025改 後追いの NS prefix 定数化(`4bd5c7e`)／M030 prefix capability 移設
  (`805f4a6`)／要求7 fail-loud 化(`ef5b645`)／interrupt aaa.md triage(`7a0c178`)／M035 マップ(`c3b0a07`)。

## 進行中の決定・考慮事項

- **【残・M036】error-swallow 監査**: 「黙って既定値を返す」握り潰しの是正が残＝`S3KeyValueStore.exists`／
  `NatsObjectKeyValueStore.exists`・`iter_all` の `except Exception: return False/[]`、`watcher` ループ等。route
  handler の `except→error 応答`（変換）は対象外。要求7 fail-loud 方針の残適用。
- **【要相談】aa.md 要求2＝plan ドキュメントの置き場所**: `m019/m021/m025/m028/m035-*-plan.md` が MB 直下に増殖。
  案＝`.work/skills/flow-memory-bank/plans/` サブディレクトリへ集約（MB 直下はコア 6＋reference に寄せる）。ユーザーと
  確定してから移動（勝手に動かさない）。
- **【決定済】要求7=fail-loud**: 暗黙フォールバック禁止。capability 非対応は loud 失敗・非 native は明示 opt-in
  （M030/M036 で iter_prefix に適用済）。
- **Memory Bank は Cline 準拠の 6 コアファイル**。運用は共通スキル `flow`（旧 memory-bank）。`.work/` は gitignore
  しない（状態の正本＝commit）。コミットは「切りのいいところ」でコード＋MB を 1 コミット、`main` 直は避け `agent`
  ブランチ単線、push は明示時のみ。
- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない（YAGNI）。
- **worker/supervisor**: 本 repo は dotfiles（`workers_dir: workers`）配下の worker。下り=interrupt 投函／上り=
  `outbox/` へ pull 型エスカレ（親は直接知らない）。

## 次のステップ

- ユーザーの M035 IDE refactor 完了を見て protocols.py 抽出（要求3）を仕上げる／aa.md 要求2 の置き場所を確定。
- 実装サイクル候補: M036（error-swallow 監査）／M037（e2e 即 skip）／フェーズ2 `kv/json`／M032・M033・M034。

## 重要なパターン・好み / 学び

- **フローは全て interrupt を介す＋参照系は reference/**: 対話の作業要望も着手前に interrupt へ書き出してから取り込む
  （funnel）。横断要件は `reference/` に集約（品質方針＝`reference/quality-policy.md`）。
- **品質チェックは組織の品質方針に従う**: 検証は `make` 経由（`make check`）＝ベタ書き `uvx ruff …` 禁止（再現性）。
- **設計原則の正本は repo の `docs/architecture.md`**（FileStore=KVS+IO・核は native primitive 側・conformance）。
  Memory Bank は一時記憶ゆえ要約のみ。
- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる（過去バグ）。
- KV=バッファ概念／FileStore=バッファ無し概念。真の streaming はクライアント wrap で得る（サーバ越しに無理に通さない）。
