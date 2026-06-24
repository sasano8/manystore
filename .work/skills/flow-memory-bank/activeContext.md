# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）に
> 畳む（2026-06-24 memory clean 実施＝旧フォーカス 13 ブロックを progress へ集約・削除）。

## 現在のフォーカス

**実装/プロトコル分離リファクタ（M035）＋ conformancer 化（M031）をユーザーが IDE で駆動中（2026-06-24）。**

> ⚠️ **ツリーが現在 red（ユーザーの in-flight 改名）**: `stores/async_storage.py` が `base.py` へ未改名なのに
> `conformancer/__init__.py:40` が `from .stores.base import`（しかも相対レベル誤り＝`..stores.base` が正）を参照し
> `test_conformance.py` が collection error。`kv.py` 等はまだ `stores.async_storage` を import＝**改名が中途**。
> エージェントは触らない（ユーザーの IDE refactor 領域）。ユーザーが async_storage→base 改名を完了させれば解消。

- ユーザーが IDE refactor で move-symbol を実行中＝コミット `60f2405`(async_storage→base)/`a37abde`(array)/
  `862f824`(async_to_sync→sync_bridge)/`61e2d43`(safe_path→safe)/`fd6ef1a`(conformance→conformancer)。
  現状 `manystore/stores/` に `async_storage.py`/`array_storage.py`/`async_to_sync_storage.py`/`safe_path.py` が移動済
  （import は `from .stores.* import` に追従・`import manystore` OK）。`sync_storage.py` は root 残置（純 Protocol）。
- **M035 移動マップの正本＝`m035-impl-protocol-split-plan.md`**（src→dest を symbol 単位で確定）。残＝Protocol を
  root `protocols.py` へ抽出（async_storage の Protocol 群＋sync_storage 全 Protocol）／subpackage 名・base.py 等への
  最終リネーム。**aa.md 要求3（sync/async プロトコルを共通管理場所へ）はこの protocols.py 集約に一致**。
- エージェント側の役割＝マップ提示済み・MB hygiene。コードの move 主体はユーザー（IDE）なので衝突を避ける
  （`.work/` のみ触る）。

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
