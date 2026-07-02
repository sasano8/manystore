# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

**M071（公開 1 Store 統合）を段階実装中＝Stage 1〜5 完了・次は Stage 6（docs 更新）**（2026-07-02 で区切り）。
設計は `plans/m071-unify-store-plan.md`（6 段階・各段 alias で非破壊）。完了＝(1)backend 1 クラス `S3Store` 等
(2)BackendSpec 単一 factory (3)アダプタ非推奨化 (4)公開型 `AsyncStore`＋`manystore.store` facade
(5)**M073 spec/impl 分離＝`manystore/spec/`（`protocols`=純粋な契約/型・`base`=既定実装＝`*StoreBase`/アダプタ/IO/`_kv_*`/
`_sha256_hex`・`exceptions`）へ集約、旧 top-level `protocols.py`/`exceptions.py` 削除、全 importer を `from ...spec import`
へ追随、後方互換 shim なし全面移行**（make check 緑 271）。さらに **M073(B) conformancer 同居 完了（2026-07-02）**＝
`manystore/tools/conformancer/` を **`manystore/spec/conformancer/`** へ丸ごと移設（カタログも検証ハーネスも一括で spec 配下）・
旧パス（`manystore.tools.conformancer`／`manystore/protocols.py`）を CLI/scaffold/文字列/Makefile/手書き docs で新パスへ・
自動生成 docs 再生成・旧 `manystore/tools/` 削除（make check 緑 271・test-heavy 緑 44・mkdocs --strict 緑）。**残＝Stage 6
（docs の概念更新＝architecture/backend_registry/implementing_a_backend を「spec が単一源泉・実装は base・1-Store」へ）**。
Stage 1〜5＋M073(B) は独立コミット・`make check` 緑済＝いつでも安全に再開可。

次サイクル候補（M071 と別筋）＝**M051（k8s secrets backend＝当初 5 要望の最後）** ／ M076（nats-fake）／
**M078（横断関心のミドルウェア合成層＝logging/retry/metrics/cache・M014/M015 包含・M071 完了後 doc-first）**。
詳細は progress「残作業」。

**M070 完了（2026-07-02）＝構成ファイルからストア復元**（`manystore store init`＋`open_store("ctx")` 名前解決・
local 相対は構成 dir 基準・上方向 discovery・serving と neutral `storage/config.py` を共有）。**URL/registry/config の
一次シリーズ（M068→M069→M070）完了**。次サイクル候補＝**M051（k8s secrets backend）**／**M074（conformance を
real/fake/fault 切替＝fake を provider 化・nats/s3 fast cov）**。大型 doc-first＝M071（公開 IF 統合＝`BufferedStore`/
`StreamingStore`）＋M073（仕様集約）は protocols.py を一括再構成。詳細は progress「残作業」。

> 品質強化フェーズ（M054〜M066）は完了。2026-06-28 の「拡張後回し」は一段落を受けて解除＝拡張系（M068 起点の
> URL/registry/config、続いて M051 k8s）へ移行。M071（公開 IF 統合＝Buffering/Nobuffering 再編）は大型・API 破壊
> ゆえ URL 系が落ち着いた後の doc-first 大型 milestone として backlog。

**北極星＝conformance を仕様の単一源泉に**（projectbrief「北極星」）。実装漏れは conformancer に契約として実装し
backend 横断で検知する。**①〜④＋fail-loud＋非CAS並行＋run_full まで完備**（M065/M066 完了・2026-06-29）:
①テスト可能（`assert_*`／`run_light·middle·heavy·full`）②pytest-cov 可視（`make cov`・TOTAL ベースライン 77%）
③spec 文書生成（`docs/conformance_spec.md`・絶対契約は `ABSOLUTE_CONTRACTS` 宣言／差分観点は run_* 実行から導出）
④scaffold（`--scaffold`＝契約一覧が実装の TODO）。fail-loud は in-process／transport（HTTP・実 leaf nats/s3）双方で
契約化。絶対契約＝writer all-or-nothing・CAS 並行（create/update）・**非CAS 並行（無条件上書き原子性・並行 delete/get
安全）**・fail-loud。全契約を 1 か所宣言（`tests/conformance_providers.py`）→ `tests/test_conformance_matrix.py` が
全 provider（dict/local/remote/実 nats/s3）へ非破壊（uuid 名前空間スコープ）で流す。

**直近サイクル（2026-06-30）**＝M061（実 backend e2e を CI で gated 実走＝skip 許容やめ）。docker compose で
nats/seaweedfs/minio を起こし CI に e2e ジョブ追加。skip マスク撤去で実問題 2 件を炙り出し是正（nats 並行
delete/get ハング→get の境界化＋レース判定／SeaweedFS の CAS 非対応→s3 実装マトリクス化＋能力差を xfail strict）。
`make check` 緑（215）・slow 41 passed/2 xfailed/9 skipped・mkdocs --strict 緑。**品質強化フェーズ（M054〜M066）完了**。

**次サイクル候補**＝機能・完成度（M013/M012/M021残/M025残 等）。詳細は progress「残作業」。

## 直近の変更

> 完了マイルストーンの詳細は `progress.md` に集約。ここには溜めない（クリーン済）。

- M061 完了（実 backend e2e を CI で gated 実走・skip マスク撤去・nats 並行 delete/get 修正・s3 実装マトリクス）。詳細は progress。
- M067 完了（download の整合性検証＝`Verify` ビットフラグで size 必須・hash あれば追加・`IntegrityError`）。残 B＝hash メタ充填は M013 連動。
- 2026-07-02 triage：URL/config/plugin/k8s の要望を M068-M071＋既存 M051 へ振り分け（interrupt archive 済）。
- **M068 完了**（2026-07-02）＝backend レジストリ/プラグイン（registry.py・group `manystore.stores`・builtin seed＋
  `manystore` remote seed・clobber 保護・トップ export）。詳細は progress。M071 命名確定＝`BufferedStore`/`StreamingStore`。
- **M013 残B（sha256 メタ充填）完了**（2026-07-02＝前セッションの未コミット分を検証して確定）。put 時 sha256 を native
  メタへ→head 露出→HTTP 透過→array 透過、conformance `meta.sha256_correct` 追加。残＝content-type/汎用 metadata。
- **M071 ステップ1**（2026-07-02）＝コア Protocol/基底を改名（`Async/SyncKeyValueStore→…BufferedStore`・
  `Async/SyncFileStore→…StreamingStore`・`KeyValueStoreBase→BufferedStoreBase`・`FileStoreBase→StreamingStoreBase`）。
  ユーザー IDE リネーム＋私が文字列/docstring/docs/生成 spec/scaffold base_name を追随。make check・test-heavy 緑。
  **過渡状態**＝facade/factory/backend 名/概念語は未改名（残ステップは progress M071 row）。
- **M077 完了**（2026-07-02）＝conformance provider を registry 駆動＋`BackendProfile` 宣言に（新 backend は
  「registry 登録＋profile 1 行」で自動参加・per-open は custom opener）。make check 緑・test-heavy 実 backend 緑。
- **M074 完了**（2026-07-02）＝conformance real/fake/fault 切替＋backend 実装ガイド（s3-fake 非 gated・CAS 非権威
  xfail・`tests/fakes.py`・`docs/implementing_a_backend.md`）。nats-fake は JetStream メタ忠実化で M076。M075 完了＝CLI Typer 化。
- **M070 完了**（2026-07-02）＝`manystore store init`＋`open_store("ctx")`（構成 dir 基準の local 相対解決・上方向
  discovery・serving と neutral config 共有）。CLI サブコマンド化（旧 `--config` は serve へ後方互換）。docs/store_config.md・test 10。
- **M069 完了**（2026-07-02）＝`open_store(url)`（fsspec 風・`storage/url.py` の `parse_store_url`・netloc=bucket 統一・
  資格情報は query 可/boto 既定へ委任・既存 flat opts へ写して後方互換）。docs/url_scheme.md・test 16・make check 248。
- **M074 登録**＝conformance を real/fake/fault で切替可能に（fake を非gated provider 化＝nats/s3 の fast カバレッジも揃う）。
- **M072 完了**（2026-07-02）＝local `delete` TOCTOU 修正（`unlink(missing_ok=True)`）＋iter_all stat race ガード。
  ユーザー方針「非一貫は確定的に赤」に応え `assert_concurrent_delete_safe` を rounds 反復強化（検出 ~12%→ほぼ確定）＋
  iter_all は monkeypatch で確定再現テスト。full fast x5 flake 0。**次は M069（名前 URL スキーマ取得）**。

## 次のステップ

- **機能・完成度**（品質強化フェーズ完了後の本流）: M013（メタデータ/content-type）/ M012（pagination）/
  M021残（S3 GW）/ M025残（名前空間 P2/P3）。
- **横展開（任意）**: s3 実装マトリクスに real AWS / 他 S3 互換を足す余地（`S3_IMPLS` に 1 行＋unsupported 宣言）。

## 進行中の決定・考慮事項

- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない（YAGNI）。拡張は doc-first 合意。
- **worker/supervisor**: 本 repo は dotfiles（`workers_dir: workers`）配下の worker。下り=interrupt 投函／
  上り=`outbox/` へ pull 型エスカレ（親は直接知らない）。
- **MB 運用**: Cline 準拠 6 コア＋`plans/`（完了 plan は削除・残フェーズの plan のみ保持）。`.work/` は commit、
  コミットは「切りのいいところ」でコード＋MB を 1 コミット、`agent` ブランチ単線、push は明示時のみ。

## 重要なパターン・好み / 学び

- **設計原則の正本は repo の `docs/architecture.md`**（FileStore=KVS+IO・核は native primitive 側・conformance）。
  Memory Bank は一時記憶ゆえ要約のみ。
- **フローは全て interrupt を介す＋参照系は reference/**: 対話の作業要望も着手前に interrupt へ書き出してから取り込む。
- **品質チェックは `make` 経由**（`make format`/`make test`＝fast・`make test-all`＝全部）。ベタ書き `uvx ruff …` 禁止。
  ※`make test`（fast）は lint を回さない＝format ドリフト（特に CJK 行の E501）は別途 `make format` で検出。
- **3.14 前提で `from __future__ import annotations` は全廃**（PEP 649 で前方参照は valid）。新規ファイルにも入れない。
- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる（過去バグ）。
- KV=バッファ概念／FileStore=バッファ無し概念。真の streaming はクライアント wrap で得る。
- **⚠️作業環境の異常（再発・要ユーザー報告）**＝`except (A, B):` が py2 構文 `except A, B:`（SyntaxError）へ
  外部から書き戻される（原因不明・hook/インジェクション疑い）。**回避＝複数例外の括弧 catch を避け単一クラス
  catch**（`except JSNotFound:` 等）。M046/M054 で再発。
