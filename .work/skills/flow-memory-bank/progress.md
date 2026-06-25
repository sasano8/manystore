# Progress

> 完了マイルストーンは**要点1行**に畳む（実装の経緯は git 履歴）。残作業は着手できる粒度で保持する。
> 設計原則の正本は repo `docs/architecture.md`、公開 API の正本は facade `manystore.kv`/`manystore.file`。

## 動くもの（What works）

- **2 ストア抽象**（`KeyValueStore` / `FileStore`＝KVS + open_reader/open_writer）と backend
  （local / s3 / nats / http〔read-only〕 / dict(memory)〔依存ゼロ・揮発の参照 backend〕）。S3/NATS/HTTP/dict すべて
  完全な FileStore（KVS+IO）に準拠。核は native primitive 側（S3=file 寄り native streaming／NATS・HTTP・dict=kv 寄り
  buffer 合成／Local=file 寄りで `LocalKeyValueStore = KeyValueFromFileStore(LocalFileStore)`）。
- **get の duality**: primitive `get_or_raise`（欠損→`FileNotFoundError`）＋基底 `KeyValueStoreBase` が `get(default)` を
  供給。`get_or_raise` は `@abstractmethod`＝実装漏れはインスタンス化時 `TypeError`。client/service 層も具備。
- **prefix は optional capability**（`SupportsPrefixListing`）＝S3 native／他は `scan_prefix` で明示 opt-in。
  非対応は **fail-loud**（`NotImplementedError`・暗黙フォールバック無し）。Safe/Array が委譲伝播。
- async / sync ブリッジ（`AsyncToSyncKeyValueStore`）、接続ライフサイクル（`connect_key_value_store`/`connecting`/
  `ConnectPolicy`）、安全パス（`validate_safe_path`/`SafeKeyValueStore`/`SafeFileStore`）、合成（`ArrayKeyValueStore`/
  `DownloadCache`）。
- **適合性ツール `manystore.tools.conformancer`**: メソッド存在チェック＋`FileStoreTester`（DictFileStore をオラクルに
  差分検証。`run_light` 実装済＝12 観点・副作用も記録・`save_report` で JSON 保存）。
- **UI/サーバ（`manystore[server]` extra）**: `serving.services`/`serving.server`/`client` の 3 層。任意 context を HTTP+WS で公開する
  汎用 CRUD UI。context = ArrayStorage 第一階層（bucket）。native REST は `{bucket}/{path}`、WS ライブ通知、
  `views.featured` で重点パス。`RemoteKeyValueStore` でサーバ越し KVS。
- **S3 互換ゲートウェイ `manystore.serving.gateway`**: GET/PUT/HEAD/DELETE/ListObjectsV2 + Multipart を `StorageService` 上に
  1:1 合成（コア IF 不変・実 aiobotocore 往復で検証済）。
- **統合エントリポイント `manystore.combined`（`python -m manystore`）**: native REST/WS（`/kv/raw`・buffered）と
  S3 ゲートウェイ（`/storage/s3`・streaming）を単一 lifespan で束ねる。
- **CI**: GitHub Actions で `make check`（`ci.yml`）。**テスト軽重分離**＝`make test`（fast・`-m "not slow"`）/`make test-all`（全部）。
- **Docs サイト（GitHub Pages・2026-06-25）**: `pages.yml` で MkDocs Material をビルド＝`make docs`（先に `make conformance-docs`
  で spec 再生成 → `mkdocs build --strict`）。PR はビルド検証のみ・**main push のみ公式 Actions（upload-pages-artifact/
  deploy-pages）で実公開**。`index.md` は snippets で README を取り込む単一ソース。docs 依存は `docs` group（mkdocs-material）。
  **要手動: Settings → Pages → Source = GitHub Actions を有効化**（初回のみ・ユーザー作業）。
  直近 fast = **120 passed, 12 deselected**。実 backend E2E（NATS / S3 path-style）検証済（`make e2e-up`）。
- **local backend は非ブロッキング**（M010）: 全 IO syscall を anyio でスレッドへオフロード＝event loop を塞がない。

## 残作業（What's left）— バックログ

優先度順。着手時は activeContext.md「現在のフォーカス」に展開する。

| ID | タスク | 優先 | 備考 |
|----|--------|------|------|
| M012 | `list(prefix=...)` / pagination | 中 | prefix は M030 で capability 化済。継続トークンページングが未対応（M021 の continuation と関連）|
| M013 | メタデータ / content-type | 中 | S3・NATS は native 対応だが共通 IF に無い |
| M016 | テスト拡充（エラーパス/並行/大容量） | 中 | fake は happy path 中心 |
| M014 | 操作レベル retry/timeout | 低 | 現状 connect のみ |
| M015 | logging（操作・リトライ可視化） | 低 | 観測性なし |
| M021残 | S3 ゲートウェイ 残 | normal | S1/S2（GET/PUT/HEAD/DELETE/ListObjectsV2 + Multipart）実装済。残＝S3 passthrough（`SupportsPresign`+redirect/proxy）/ S4 SeaweedFS 実機 backend 疎通 / ListObjectsV2 continuation token ページング。設計 `plans/m021-s3-gateway-plan.md` |
| M022b | conformance の run_middle/heavy/full ＋ spec（file/kv 寄り）検出・特性表・リプレイ | low | P1 存在チェック＋P2 run_light 完了。`tester.spec={"leaning":None}` は placeholder。実 backend（S3/NATS）適用も |
| M027b残 | FileStore=KVS+IO 波及（Sync 残） | low | S3/NATS/HTTP/Local＋`SafeFileStore` 完了（Safe は M032 で `SafeKeyValueStore` 継承＝KVS 面も検証付き委譲に）。残＝`SyncFileStore` Protocol 鏡映＋`AsyncToSyncFileStore` ブリッジのみ |
| M025残 | 名前空間再編 フェーズ2/3 | normal | フェーズ1（移設）＋addressing 再設計 完了。残＝フェーズ2 `kv/json`（JSON 検証）/ フェーズ3 `storage/manystore`（range/chunked streaming）。設計 `plans/m025-namespace-restructure-plan.md` |
| M026 | stream インターフェース（第3の族・新コア IF） | 相談 | kv/storage の他に **stream**＝無境界チャネル（append/follow＝tail/subscribe）。FileStore で表せない＝新コア IF `StreamStore`。MVP=byte stream。最小・汎用と緊張するので **doc-first 合意必須**（着手時に設計を起こす。旧 interrupt は GC 済＝git 履歴に残存）|
| M028b | ArrayStorage を HTTP に動的公開（context の mount/unmount） | low | `POST/DELETE /contexts` で動的 mount。backend 資格情報を HTTP から渡す＝認証設計が要る（M011 連動）。**動的化の核**＝非同期 `attach`/`detach`（connect→登録 / 登録解除→aclose を `asyncio.Lock` で直列化。並行 POST/DELETE の競合・リーク防止）。`mount`/`unmount` の IF は既に非同期化済（中身は登録のみ）＝ロック実装を後付けできる。要設計 |

> **ゴール段階**: G1=配布できる（M005〜M008 完了）→ G2=安心して使える（M009〜M011・M016）→
> G3=機能十分（M012〜M015）→ G4=広く使える（M017 判断）。

### 完了マイルストーン（要点のみ・経緯は git 履歴）

- **M001〜M004**: 旧 `shoudou_storage` 残骸掃除 / 実 backend E2E（NATS・S3 path）/ CI＋lint 統一 / README。
- **M005〜M008**（配布前提 G1）: 未使用依存 `redis` 削除 / LICENSE=MIT / PyPI メタ整備。**M007 py.typed は不採用**
  （型チェッカが公開 API を厳格化し運用コスト増＝ユーザー判断）。
- **M009**: 統一例外階層 `ManystoreError`（`manystore/exceptions.py`）＝`status/title/type`＋`to_problem` で RFC 9457
  Problem Details に変換。native REST のエラー応答を `application/problem+json` 化（S3 GW は S3 互換 XML のまま）。
- **M018**: HTTP backend（read-only・`backends/http_store.py`・httpx 遅延 import）。
- **M019**（UI P1〜P3）/ **M020**（UI パンくず＋生パス編集）: 完了。残 P4(http RW)/P5 等は M021 等へ移管（plan は GC 済）。
- **M021 S1/S2**: S3 ゲートウェイ + Multipart（予約キー空間 `.manystore-mpu/...` で状態管理）。残は上表 M021残。
- **M022 P1/P2**: conformance メソッド存在チェック＋`FileStoreTester.run_light`。残は上表 M022b。
- **M023**: native REST + S3 を単一 FastAPI に統合（`include_router(prefix=)`・共有 service 単一 lifespan）。
- **M025 フェーズ1＋改**: 名前空間を buffer 性で再編（`/kv/raw`・`/storage/s3`）＋native を `{bucket}/{path}` addressing に。残は上表 M025残。
- **M027 / M027c**: Local の KV を `KeyValueFromFileStore(LocalFileStore)` 派生に（真実は FileStore 側に集約）。
  get_or_raise primitive 化を client/service へ波及（`KeyValueStoreBase` を ABC 化）。
- **M028**: HTTP の context を `ArrayKeyValueStore` バックに（mount で振り分け・横断列挙を委譲）。`plans/` から削除済。
- **M030**: prefix を `SupportsPrefixListing` capability に移設（直後 M036 で fail-loud 化＝暗黙フォールバック撤去・`scan_prefix` 明示 opt-in）。
- **M031**: `conformance.py`→`conformancer/`（ユーザー IDE refactor）。残＝内部分割の整理。
- **M034（2026-06-25・完了）**: conformancer に CLI 入口 `python -m manystore.tools.conformancer`（`__main__.py`）を新設。
  メソッド存在チェック（接続不要・決定的）で各実装 × メソッドの Implemented/Not を `docs/kv_spec.md` /
  `docs/file_storage_spec.md` へ生成。`make conformance-docs` でキック。挙動ベースの spec 検出は M022b に残置。
- **M035**: 実装を `manystore/stores/` へ分類（base/array/safe/sync_bridge）＋`conformancer/`。完了 plan 削除。
- **M037**: テスト軽重分離（`@pytest.mark.slow`・`make test`/`test-all`）＋未整備依存の早期 skip。fast ~0.65s。
- **protocols.py 集約（2026-06-25）**: `stores/base.py` 削除＋既定実装を protocols.py へ全面集約（詳細は systemPatterns）。
- **M033（2026-06-25）**: `iter_all`/`list_all` の limit 統一は全面波及済と確認＝全 backend・全ラッパ
  （Safe/Array/sync_bridge/remote）が `limit:int|None` シグネチャで forward、`list_all` は `iter_all(limit)` 参照。
- **M036（2026-06-25・完了）**: error-swallow を fail-loud 化。`nats.iter_all`（空ストアの `NotFoundError` のみ []・
  他は伝播）／`nats.exists`・`s3.exists`（not-found〔NotFoundError／404〕のみ False・認証/5xx/接続断は伝播）。
  test +3（s3 非 404 伝播・nats 空は非エラー・nats 実エラー伝播）。watcher ポーリングは意図的レジリエンスとして存置。
  ※nats の not-found catch 自体を将来 `obs.watch()` ベース再実装で取っ払う TODO をコード内に残置（ユーザー判断で
  今回は現状維持＝not-found→[]/False の正規化は契約上必要）。
- **M024（2026-06-25）**: pull 型エスカレ（outbox）の文書追従完了＝MB に push 前提の残記述なし・旧スキル名なし・
  alias を `[[unit-quality]]` に統一。
- **M011（2026-06-26・完了）**: 安全入口の最終形＝**入口の命名マトリクスを確定**（2 コミット）。
  - **②責務分離（C1）**: `ArrayKeyValueStore.mount`/`unmount` を**登録のみ（I/O なし）**に分離（mount が
    connect も担う二重責務を解消）。接続は顔 `open_async_array_store(mounts)` の CM が一括で担う。`StorageService.connect`
    は明示 connect + 同期 mount に追従。
  - **①命名（C2）**: 低レベル factory を `create_key_value_store`/`create_file_store` →
    **`create_unsafe_key_value_store`/`create_unsafe_file_store`** にリネーム（名前で unsafe＝キー検証なしを明示）。
    **`create_safe_{key_value,file,array}_store`** 新設（Safe 包装のみ・未接続）。**生口はトップ公開に残す**
    （ユーザー確定＝格下げせず名前で明示のみ）。caller 全追従（connect/service/config/README/tests）。test +1。
  - 完成した 3×3 マトリクス: **unsafe**（生・未接続・キー検証なし）/ **safe**（Safe 包装・未接続）/
    **open_async**（顔＝Safe 包装＋接続 CM）× kv/file/array。open_async は内部で create_safe_* を呼ぶ（dedup）。
- **M010（2026-06-25・完了）**: local backend を非ブロッキング化＝`storage/backends/local.py` の同期 IO
  （open/read/write/close・rglob+stat・replace/unlink・mkdir）を `anyio.to_thread.run_sync`（`_offload`）で
  ワーカースレッドへオフロードし event loop を塞がない。`_LocalAtomicWriter` は構築（mkstemp/fdopen）も
  syscall ゆえ async ファクトリ `_LocalAtomicWriter.open()` 経由に変更（atomic temp+replace は不変）。
  方式は **anyio**（スレッドプール系・新規依存ゼロ＝httpx 経由で在中だが明示依存に格上げ `anyio>=4.0.0`）。
  真の async disk IO（aiofile/libaio）は不採用＝移植性・最小・YAGNI 優先（buffered では native AIO も
  スレッドへフォールバックし実効差小）。`__init__` の resolve/mkdir は構築時一回限り＝ホットパス外で据置。
- **M032（2026-06-25・完了）**: 安全な入口（ライブラリの顔）を新設＝`open_async_key_value_store` /
  `open_async_file_store`（トップ公開）。**Safe 包装必須の接続 CM**（`async with` で connect＋`Safe*` 包装、
  終了で aclose。`policy`/`verify` も受ける）。併せて `create_file_store`（FileStore 版ファクトリ）新設と
  `SafeFileStore` を `SafeKeyValueStore` 継承に作り直し（= KVS 面も検証付き＝M027b の Safe 残も解消）。
  生ストアは `create_*`/`connect_*`（低レベル）に残置。test +4。

## 現状ステータス

独立ライブラリ化＋配布前提（G1）完了。コア抽象は「FileStore=KVS+IO・核は native primitive 側・get duality・
prefix capability（fail-loud）」で安定。protocols.py が契約＋既定実装の単一源泉。次は G2（安心して使える＝
error-swallow 監査 M036 / Safe 既定化 M011・M032 / テスト拡充）と UI/GW の残フェーズ。

## 既知の問題

- `s3-virtual`（ドメインスタイル）はローカル S3 互換では `bucket.<host>` を名前解決できず常に skip。
  **virtual-host の仕様上の制約**（実 AWS 等の DNS 環境向け）であり未解決バグではない。
- `make test`（fast）は lint を回さない＝format ドリフト（特に CJK 行の E501）は `make format` でしか出ない。

## 意思決定の変遷

- ストレージ抽象は独立ライブラリとして自己完結。利用側固有の結線は利用側 adapter に閉じ、本体は最小・汎用に保つ。
- **S3 アドレッシングスタイルを明示パラメータ化**（既定 virtual／利用側が `"path"` opt-in）。fake では気づけず実機 E2E で露見。
- **Python 3.14+ 前提に確定**: PEP 649（注釈遅延評価）が既定ゆえ前方参照は valid＝`from __future__ import annotations`
  全廃。`requires-python>=3.14`＋ruff `target-version=py314`。ruff は py314 対応必須＝`RUFF_VERSION=0.15.18`。
- **fail-loud（要求7）**: 暗黙フォールバックで失敗・非対応を握り潰さない。capability 非対応は loud 失敗・非 native は明示 opt-in。
- **protocols.py = 契約＋既定実装の唯一の源泉**（2026-06-25）: backend が継承・流用する base/adapter/helper を 1 ファイルに集約し
  二重参照を断つ（`stores/base.py`・`sync_storage.py` 削除）。
- **ディレクトリを 3 バケットに再編**（2026-06-25・ユーザー IDE）: `storage/`（ライブラリ本体＝backends・surfaces〔旧 stores〕・
  facade kv/file）/ `serving/`（HTTP 公開＝services〔旧 implement〕・server・gateway）/ `tools/`（conformancer）。`implement`
  の曖昧な名前を解消し「ストレージ抽象」と「その serving」をトップで分離。`protocols`/`connect`/`exceptions`/`client`/`combined` はトップ据え置き。
- **Python サポート範囲は 3.14+ で確定（M017 見送り・2026-06-25）**: 3.10+ へ広げる案は YAGNI で見送り
  （広げると future import 復活＋ruff 設定の負担。3.14 純度を優先）。需要が出たら再検討。
- Memory Bank: Cline 準拠 6 コア。作業フォルダ `.work/skills/flow-memory-bank/`（`.work/` は commit する正本）。
  完了 plan は削除し、残フェーズの plan のみ `plans/` に保持。
