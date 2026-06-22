# Active Context

## 現在のフォーカス

**M021 S1 を「実 S3 クライアント往復」で検証補強（2026-06-23 後続サイクル・supervisor 指示）。**
S1 の既存テストは gateway 生成の S3 XML を stdlib ElementTree でパースするだけで**実 S3 クライアント往復が無かった**。
直前実装者は「実 client = 同期 boto3 は新依存」として S4 へ繰越したが、**manystore はコア依存に `aiobotocore>=2.0.0`
（botocore 内包の実 S3 クライアント）を持つ**ため、`endpoint_url=<起動 gateway>` に向ければ**新依存ゼロ**で実往復が
書けると判明＝前倒し解消。

- **追加テスト**: `tests/ui/test_gateway_s3client.py`（4 ケース）。
  - `test_real_s3_client_roundtrip`: PUT→GET→HEAD→ListObjectsV2(flat)→DELETE を aiobotocore で往復。ETag（PUT/GET/HEAD
    一致）・本文一致・ContentLength・削除後 GET=NoSuchKey を検証。
  - `test_real_s3_client_list_delimiter_common_prefixes`: delimiter='/' で CommonPrefixes 畳み、prefix+delimiter で
    1 階層下の Contents 列挙を実クライアントで検証。
  - `test_real_s3_client_get_missing_raises_nosuchkey`: 欠損キー GET が `NoSuchKey` 例外（XML パース経路）に。
  - `test_real_s3_client_readonly_bucket_access_denied`: writable=false への PUT が `AccessDenied`（ClientError）に。
- **起動方式**: aiobotocore は**実ソケット**を使うので in-process ASGI ではなく **uvicorn を ephemeral port
  （127.0.0.1:0）で別スレッド起動**するフィクスチャ（`_ThreadedServer`・`server.started` を待って endpoint を返す）。
  gateway は `GET /{bucket}/{key}` ＝ bucket をパスに置く **path-style** 前提なので client は `addressing_style="path"`。
- **依存の扱い＝新依存ゼロ**: aiobotocore（3.7.0）はコア依存、uvicorn（0.49.0）は `[server]` extra/dev 既存、
  pytest-asyncio は既存（`asyncio_mode=auto` で `async def test_*`）。**追加インストール不要**。同期 boto3 は入れていない。
- **実クライアント往復で判明した齟齬＝ゼロ**: 実 botocore は XML/ヘッダ厳密性に敏感だが、S1 が生成する ETag・
  Content-Length・XML 名前空間（`http://s3.amazonaws.com/doc/2006-03-01/`）・エラー Code（NoSuchKey/AccessDenied）・
  ステータスコードを **botocore がそのまま受理**＝不具合・差異なし。S1 の XML/ヘッダ実装が実クライアント基準で妥当と実証。
- **検証**: `make check` 緑（**76 passed, 1 skipped**・従来 72 から +4。skip は既存 s3-virtual E2E で本変更と無関係）。
  format-check も clean。
- **残課題**: S4 = **SeaweedFS 実機 backend** 疎通（実 client 往復は前倒し済み・残るは実 backend とパススルー）／
  S2 multipart／S3 passthrough。

## （旧フォーカス）

**M021 S1（S3 ゲートウェイ本体）実装完了（2026-06-23・supervisor interrupt 指示）。** manystore を S3 互換 API
として公開する新サブパッケージ `manystore.gateway` を追加。`m021-s3-gateway-plan.md` の S1 のみをスコープ厳守で実装
（S2 multipart・S3 passthrough・S4 SeaweedFS 実機・繰延ページング・残未決 Q1/Q4/Q5 はバックログ）。

- **新規ファイル**: `manystore/implement/s3map.py`（delimiter 畳み込み + S3 XML 生成 + エラー XML。HTTP 非依存＝
  stdlib `xml.etree.ElementTree` のみ・新依存ゼロ）、`manystore/gateway/{__init__,app,routes,__main__}.py`
  （M019 server 層と同型。`create_gateway(service)`・FastAPI 遅延 import・lifespan で connect/aclose・`[server]`
  extra 流用・`python -m manystore.gateway --config <toml>` 既定 port 9000）。
- **操作（S1）**: GET=GetObject / PUT=PutObject(ETag=MD5) / HEAD=HeadObject(Content-Length) / DELETE=DeleteObject(204)
  / ListObjectsV2(prefix+delimiter)。すべて既存 `StorageService`（put/get/exists/delete/list_entries）へ 1:1 で乗せる
  ＝**コア IF 不変**。bucket=context。delimiter は s3map で CommonPrefixes に畳む（service.list_entries は delimiter
  非対応なので gateway/s3map 側で畳む）。例外→S3 エラー XML（ContextNotFound→NoSuchBucket / ReadOnlyContext→
  AccessDenied / UnsafePathError→InvalidArgument / get None→NoSuchKey）。
- **推奨デフォルト適用**: Q2 SigV4 検証=しない（gateway 認証へ委譲）、Q6 extra=`[server]` 相乗り、Q3 ページング=
  max-keys 上限 1000 クランプ・打ち切りのみ（continuation token は繰延）。Q1/Q4/Q5・S2/S3/S4 はスコープ外。
- **テスト**: `tests/ui/test_gateway.py`(8・local backend に対し PUT/GET/HEAD/DELETE/ListObjectsV2・delimiter 畳み・
  各種 S3 エラー XML)・`tests/ui/test_s3map.py`(5・純ロジックの fold/XML)。`make check` 緑（**72 passed, 1 skipped**・
  従来 59 から +13）。⚠️ 実 S3 client（boto3/aws-cli）疎通は **S4 へ繰越**（aiobotocore は async-only、同期 boto3 は
  新依存になるため S1 では XML を ElementTree パースで検証。実 client/SeaweedFS 疎通は S4 段階）。

## （旧フォーカス）

**M020（UI 改善: パンくず階層ナビ + コピー/生パス編集）完了。** ユーザー要望（2026-06-21）:
(1) パスを `dir1 / dir2 / dir3` のパンくず表示にし各セグメントをクリックでその階層へ移動、
(2) 左にコピーボタン、パンくず（空きスペース）をクリックすると生パスのテキストボックスになり貼り付け可能。
ユーザー懸念「KVS に階層概念が薄い／中間階層に飛ぶと下層が分からない」への回答＝**問題なし**:
`/keys?prefix=` が prefix 配下の**全キーをフラットに返す**（service.list_entries が iter() を startswith 絞り込み）
ので、フロントで `/` 区切りに畳めば仮想ツリーになり、中間 prefix でも直下のフォルダ/ファイルを次セグメント
group で列挙できる（実機 smoke 済み: prefix='dir1/' で dir2/ と直下ファイルが見える）。
**実装は `manystore/server/static/`（index.html/app.js/style.css）のみ＝サーバ・protocol・python は不変**
（`pytest tests/ui` 8 passed・app.js は node --check 緑）。app.js は state.dir(現在ディレクトリ prefix)/
state.key(開いているファイル) を持ち、navigateTo→renderTree(フォルダ/ファイル畳み込み・`..`行)+renderBreadcrumb。
copy ボタンは clipboard.writeText（不可なら生パス入力にフォールバック）、新規/quick_write は editRawPath。

**M019（ストレージ UI）P1〜P3 実装完了。** `manystore.{implement,server,client}` の 3 層を追加し、任意 context を
HTTP+WS で公開する**汎用 CRUD ストレージ UI** を実装。`make check` 緑（**59 passed, 1 skipped**）＋実起動スモーク
（interrupt への remote PUT 往復を実証）。最終レイアウトは **単一ディストリビューション + `manystore[server]`
extra**（当初の別パッケージ案から巻き戻し。理由は `m019-ui-plan.md`）。interrupt 専用 UI は作らず、config の
`views.featured`（pin/quick_write）で重点表示する汎用 UI＝interrupt も「featured な local への汎用 PUT」で投入。

次サイクル候補: M019 残り（P4 http_store の RW 拡張 / P5 S3 gateway / LocalWatcher=inotify / 認証）、または
配布前提の G1（M005 redis 削除・M006 LICENSE・M007 py.typed・M008 メタ）。

（前タスク **M018 完了**。）
本プロジェクトは `agent` ブランチで単線コミットし、`interrupt/` 受信箱の指示を取り込んで進める運用。
dotfiles は `workers_dir: workers` を宣言した **supervisor**（自身も Memory Bank を持つ）で、
`dotfiles/workers/manystore` → 本 repo の symlink 配下に manystore を worker として束ねる。
下り（dotfiles→manystore interrupt 投函）／上り（manystore→dotfiles interrupt エスカレ）の双方向運用。

## 直近の変更

- **M021（S3 ゲートウェイ + パススルー）の着手前 deep think を実施（2026-06-23・supervisor interrupt をトリアージ）**：
  `interrupt/20260623-s3-gateway-and-passthrough.md`（priority normal）を取り込み、**実装はせず設計のみ確定**。
  成果物 `m021-s3-gateway-plan.md`。要旨＝(1) 最小 S3 操作 = GET/PUT/HEAD/DELETE/ListObjectsV2（multipart は段階2、
  bucket=context、delimiter 対応）。(2) パススルー = presigned redirect 本線＋プロキシフォールバック、署名は
  **gateway 保有の実 S3 資格情報**で代理署名（クライアント資格情報は実 S3 へ転用しない＝STS は YAGNI）。
  presign は **S3 backend 限定の optional capability `SupportsPresign`** に閉じコア IF は不変。(3) 置き場 =
  新サブパッケージ `manystore.gateway`（M019 同型・FastAPI 遅延 import・既存 `[server]` extra 流用・S3 XML は
  stdlib ElementTree＝新依存ゼロ・`implement` の `StorageService` を再利用）。(4) 段階 = S1 本体→S2 multipart→
  S3 パススルー→S4 SeaweedFS 実機。**コード未実装・git commit せず**（WIP なし。コミット判断は worker 対話/ユーザー）。
  既存 progress の M019 P5（S3 gateway）を本計画として精緻化・採番。

- **検証はベタ書き禁止＝Makefile 経由に統一（ユーザー要望 2026-06-21）**：techContext.md の「検証コマンド」を
  生 `uvx ruff …` → `make lint`/`make check` 参照に修正（ruff 版は `RUFF_VERSION := 0.15.18` で固定済み・既に
  Makefile 完備）。これが「毎回手打ち」の元凶だった。**この方針の正本は quality スキル（R5 Makefile / R8 `make check`）**。
  - **dotfiles の位置づけを再訂正（陳腐化）**：dotfiles は今や `workers_dir: workers` を宣言した **supervisor**で、
    `dotfiles/workers/manystore -> ../../manystore` の symlink 配下に manystore が worker としてぶら下がる。
    過去メモ「dotfiles は supervisor でない」は無効。
  - **親へエスカレ実施**：「quality が常時ループ（memory-bank）から参照されず発揮されない」構造ギャップを、
    親 `dotfiles/.work/skills/memory-bank/interrupt/20260621-quality-skill-not-applied.md` に worker として投函
    （memory-bank→quality のリンク追加等を提案。反映先は supervisor 判断）。親スキルは worker から直接編集しない。
- **UI 開発起動を整備**：`make ui`（= `examples/manystore-ui.dev.toml` で起動）。dev 既定ストレージは
  `.cache/manystore_dev`（`.gitignore` に `.cache/` 追加＝使い捨て・起動時に LocalKVS が自動 mkdir）。
  `PORT=xxxx` で上書き可。client SDK は `ManystoreClient`/`RemoteKeyValueStore`（`manystore.client.remote`）に改名済み。
- **公開 API 整理（ユーザー要望 3 点）**：
  - **pytest-asyncio 導入**（`asyncio_mode="auto"`）。新 UI テスト（implement/client）は `async def` 化。実害なし
    （dev 依存のみ・既存 `asyncio.run` と共存・fastapi TestClient は同期で anyio 競合なし）。
  - **名前空間グルーピング**：`manystore.kv`（値ストア）/ `manystore.file`（ファイル）facade を新設。トップは
    後方互換でフラット再エクスポート（star import + noqa、`__all__` は dict.fromkeys 重複畳み込み）。
  - **FileStore を方向別バイナリ API に置換**：`open(mode)` 廃止 → `open_reader`/`open_writer`。全 backend・
    KeyValueFileStore・SafeFileStore・SyncFileStore Protocol・`tests/test_storage.py`（一括置換）を更新。
    HttpFileStore は read-only で `open_writer` が `io.UnsupportedOperation`。`make check` 緑（59 passed, 1 skipped）。
- **M019 P1〜P3 実装（ストレージ UI）**：`manystore/implement`（protocol/config/service/watcher・backend非依存）、
  `manystore/server`（FastAPI app/routes/__main__/static・遅延 import）、`manystore/client`（ManystoreClient /
  RemoteKeyValueStore）を追加。`pyproject` に `[project.optional-dependencies] server` と dev group。
  `tests/ui/`（implement/server/client の 3 層）。`examples/manystore-ui.toml`・README 節を追加。
  - **決定の巻き戻し**：当初「別パッケージ（uv workspace）」→ ユーザー選択で **`manystore` 配下に 3 層サブ
    パッケージ＋extras** に確定（import 名前空間 `manystore.*` 統一、配布は extras+遅延 import で軽さ維持）。
  - 監視は MVP では **PollingWatcher**（size 差分で created/modified/deleted、全 backend 対応・テスト容易）。
    inotify(watchdog) ベースの LocalWatcher は後続最適化。`modified` は同一サイズ編集を取りこぼす既知制約。
- **M018 完了（HTTP backend, read-only）**：ユーザー要望「http ストレージを read-only でよいから欲しい」を実装。
  `backends/http_store.py`（GET で `get`/`open("rb")`、HEAD で `exists`。404→None/FileNotFoundError。書き込み・
  一覧は `io.UnsupportedOperation`）。httpx を遅延 import。`create_key_value_store("http", http_base_url=...,
  http_headers=...)` 配線、`__init__.__all__`・README・テスト（fake httpx client で 4 ケース）整備。
  - **モジュール名**: 当初 `http.py` で作られていたが stdlib `http` パッケージと紛れるため `http_store.py` に
    リネーム（**backend 識別子は `"http"` のまま**）。ユーザー指摘。
  - **M005 修正**: httpx は当初「未使用＝削除」だったが http backend で使うので**残す**に変更。`redis` のみ未使用。
- **プロセスの穴を2つ発見し、フックで修正**：
  - (a) ユーザー要望（http backend）が**着手前に Memory Bank へ保存されず**、前回セッションで未コミットの試作だけ
    残っていた（活動記録なし）。教訓は「要望は着手前に activeContext/タスクへ記録してから実装」。
  - (b) **より根本**：SessionStart フック `dotfiles/bin/memory-bank-sessionstart` が「`.work/skills/memory-bank/`
    の**ディレクトリ有無だけ**」を見ていた。① 完全に無ければ黙って no-op（警告なし）、② dir が在れば中身が空でも
    「Memory Bank があります」と誤検知。つまり **Memory Bank の成立条件（`.work`＋6コア）が崩れても誰も警告しない**。
    → フックを「**dir はあるがコアが欠けていれば警告して initialize を促す**」よう修正・実測検証（dotfiles 側の作業
    ツリー変更。コミットは dotfiles 側に委ねる）。完全欠如はマーカー無しに全 repo で鳴らせないので no-op のまま。
  - **dotfiles の位置づけを訂正**（※**この訂正は後に覆った**——上記 2026-06-21「dotfiles の位置づけを再訂正」参照）：
    当時は「dotfiles はスキルのホストであって Memory Bank を持つ supervisor ではない」と判断したが、**現在の dotfiles は
    `workers_dir: workers` を宣言し 6 コアの Memory Bank を持つ正式な supervisor**（manystore を `workers/` に symlink 配下）。
    下り（dotfiles→manystore interrupt 投函）は当時から実績あり（M003 の m003-ci 指示）＝この点だけは一貫。
- **UI 要望をバックログ化**：ユーザー要望「ストレージの UI が欲しい」を progress.md の **M019（相談）**へ。
  未スコープ＋本体スコープ外のため、別パッケージ/別リポか着手前に要合意。

- **juice 概念を削除**：manystore は juice と無関係な独立ライブラリなので、コード（`__init__`/`array_storage`/
  `tests`/`pyproject`/README）と Memory Bank から juice・E006・「pristine（juice 都合）」の記述を一掃。設計
  原則は「**最小・汎用に保つ（YAGNI）**」として残す。juice adapter のバックログ（旧 M005）も削除。
- **M002 一部完了**：docker（nats / seaweedfs）で `tests/test_e2e_backends.py` を**パラメタライズ**追加
  （同一 CRUD を local / nats / s3-virtual / s3-path に注入。実行 test は1つ、注入インスタンスだけ違う）。
  **local / nats は実機で pass**。S3 は実機検証で **アドレッシングスタイル問題を発見**し、`addressing_style` を
  **明示パラメータ化（既定 virtual＝ドメイン、`"path"` は opt-in）**に変更（`s3_addressing_style`）。
- **M002 完了**: SeaweedFS の S3 認証は `weed shell s3.configure` で dev identity（`manystore`/`manystoresecret123`,
  Admin）を登録して解決。`make e2e-up`（compose up + identity 登録）で 1 コマンド化し、テスト既定鍵もこの dev
  identity に。`make check` で **s3-path 実機 pass**（47 passed, 1 skipped）。s3-virtual はローカルでは原理的 skip。
- **M004 完了**：ルート `README.md` を作成（特徴・install・local/S3/NATS の接続例・`ConnectPolicy` プリセット・
  `Safe*` ラッパ・その他公開 API・開発/CI/3.14 注記）。公開 API は `manystore/__init__.py` の `__all__` に準拠。
- **M003 完了（supervisor 指示で着手）**：dotfiles（supervisor）が manystore の interrupt に投函した指示
  （`20260620-1200-m003-ci.md`, priority high）を取り込み、GitHub Actions CI（`.github/workflows/ci.yml`：
  setup-uv → `make check`）を追加。指示は `interrupt/archive/` へ退避。
- **Python 3.14+ 前提を確定**：3.14 は注釈遅延評価が既定なので前方参照（自クラス戻り値注釈）はそのまま valid＝
  `from __future__ import annotations` 不要。`requires-python = ">=3.14"` ＋ ruff `target-version = "py314"` に
  し future import を全廃。ruff は py314 対応版が要るので `RUFF_VERSION` を 0.15.18 へ。`make check` 緑（44 passed）。
- **M001 完了**：旧名残骸を監査（`git grep shoudou`）。実コードの残骸は NATS 既定バケット名のみで、
  `manystore/backends/__init__.py` の `nats_bucket="shoudou_files"`→`"manystore_files"` に変更（既定値のみ・
  テスト非依存）。`uv run pytest` で **44 passed**。
- 本セッションで `Makefile`（`uvx ruff@<固定版>` の format / `uv run pytest` の test）を追加（M003 の一部）。
- `shoudou_storage` を独立ライブラリ `manystore` として抽出し、import 名・プロジェクト名を `manystore` に
  統一。関連 commit: `f80ba87` / `1983fc7` / `2d28010`。
- Memory Bank を導入。当初は AGENT_LOOP.md / PROJECT.md の 2 ファイル構成だったが、
  **Cline の Memory Bank（6 コアファイル）に準拠**するよう作り直し、作業フォルダ
  `.work/skills/memory-bank/` 配下へ集約した。

## 次のステップ

- バックログ（progress.md）から優先タスクを 1 つ選定し、本ファイルの「現在のフォーカス」に展開。

## 進行中の決定・考慮事項

- **Memory Bank は Cline 準拠の 6 ファイル**（projectbrief / productContext / activeContext /
  systemPatterns / techContext / progress）。手順・運用は共通スキル `memory-bank`（`~/.claude/skills/`）に集約。
- 作業フォルダ規約は `.work/skills/<スキル名>/`。`.work/` は gitignore しない（状態の正本＝commit する）。
- **コミットをフローに組み込む**：Act Mode の終端で「切りのいいところ」（まとまり一段落＋検証緑＋
  Memory Bank 更新済み）になったら、コード＋Memory Bank を 1 コミットにまとめる。`main` 直は避け branch を切る。
  push は明示時のみ。
- **manystore は最小・汎用に保つ**：利用側都合で IF を拡張しない。利用側固有の結線は利用側の adapter に閉じる。

## 重要なパターン・好み / 学び

- **フローは全て interrupt を介す＋参照系は reference/**（memory-bank 設計変更, 2026-06-21）：対話での要望・指示も
  着手前に一旦 interrupt へ書き出してから取り込みフローで処理する（即答の雑談は除く）。横断的な参照定義・要件は
  `reference/`（ファイル/ディレクトリ）に集約し、品質方針はその 1 エントリ（`reference/quality-policy.md`）。品質以外の
  要件も reference に足せる。コア/SKILL 本体は中身を持たず reference を参照するだけ。
- **品質チェックは組織の品質方針に従う（関心の分離）**：memory-bank は「品質チェックを行う」だけ・規約を持たない。
  一般メソッドは [[quality]]、組織固有の適用は **組織の品質方針ファイル**（supervisor memory-bank `reference/quality-policy.md`）、
  本 repo の techContext はそれを **`make check` に materialize するだけ**。検証は `make` 経由＝ベタ書き `uvx ruff …`
  禁止（再現性）。スキル設計（dotfiles）も更新: memory-bank を最小化＋ reference/ 導入／quality に「関心の分離（俯瞰的/単体）」
  「ドキュメントの書き方・読み方」節＋R10/R11／supervisor が drift を定期チェック。
- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる必要があった（過去バグ）。
