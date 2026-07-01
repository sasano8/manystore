# Active Context

> activeContext は「**今の焦点のスナップショット**」。完了マイルストーンの履歴は `progress.md`（M-row）へ、
> 恒久的な設計事実は `systemPatterns.md` へ畳む。生の時系列ログはここに溜めない。

## 現在のフォーカス

**M068 完了（2026-07-02）＝backend レジストリ / プラグイン機構**（fsspec 風の土台）。次は **M069＝名前 URL
スキーマ取得（`s3://`/`manystore://`/`local://.`）＋ bucket 粒度統一**（registry の上に `open_store(url)` サーフェス、
`create_unsafe_*` の flat kwargs をネイティブ opts へ整理）。続いて M070（config 復元）→ M051（k8s）。M071（公開 IF
統合＝`BufferedStore`/`StreamingStore`）は URL 系の後の大型 doc-first。詳細は progress「残作業」M069-M071。

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
