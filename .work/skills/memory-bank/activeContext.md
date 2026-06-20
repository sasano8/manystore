# Active Context

## 現在のフォーカス

**M019（ストレージ UI）P1〜P3 実装完了。** `manystore.{implement,server,client}` の 3 層を追加し、任意 context を
HTTP+WS で公開する**汎用 CRUD ストレージ UI** を実装。`make check` 緑（**59 passed, 1 skipped**）＋実起動スモーク
（interrupt への remote PUT 往復を実証）。最終レイアウトは **単一ディストリビューション + `manystore[server]`
extra**（当初の別パッケージ案から巻き戻し。理由は `m019-ui-plan.md`）。interrupt 専用 UI は作らず、config の
`views.featured`（pin/quick_write）で重点表示する汎用 UI＝interrupt も「featured な local への汎用 PUT」で投入。

次サイクル候補: M019 残り（P4 http_store の RW 拡張 / P5 S3 gateway / LocalWatcher=inotify / 認証）、または
配布前提の G1（M005 redis 削除・M006 LICENSE・M007 py.typed・M008 メタ）。

（前タスク **M018 完了**。）
本プロジェクトは `agent` ブランチで単線コミットし、`interrupt/` 受信箱の指示を取り込んで進める運用。
dotfiles はスキルのホスト（＝skills/bin の置き場）で、manystore の interrupt に指示を投函してくる（下り）が、
dotfiles 自身は Memory Bank を持たない＝「記憶を持つ supervisor」ではない（下記「直近の変更」参照）。

## 直近の変更

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
  - **dotfiles の位置づけを訂正**：dotfiles は**スキルのホスト**（skills/bin/install.sh）であって、それ自身は
    Memory Bank（6コア）を持つプロジェクトではない。「supervisor（dotfiles）が記憶を持って指示する」は実体の
    ない過去 framing だった。下り（dotfiles→manystore interrupt 投函）は実績あり（M003 の m003-ci 指示）。
    本セッションで一旦 dotfiles に作った interrupt だけの空ディレクトリは**誤りなので削除して元に戻した**。
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

- ラッパは 1 枚、差し替えるのは backend だけ。抽象 IF を backend 固有事情で汚さない。
- NATS backend は実 nats-py の API（`get_info` / `get().data`）に合わせる必要があった（過去バグ）。
