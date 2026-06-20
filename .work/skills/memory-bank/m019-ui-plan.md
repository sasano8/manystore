# M019: manystore-ui 計画（確定）

> ユーザー要望（2026-06-21）を構想化し、スコープを合意した計画。本体スコープ外のため
> **同リポ別パッケージ（uv workspace）** として作る。詳細は本ファイル、サマリは progress.md M019。

## 確定したスコープ判断（2026-06-21、AskUserQuestion）

1. **置き場所** = 同リポの別パッケージ（uv workspace member）。manystore 本体は汚さず、
   `manystore` を workspace 依存として参照。
2. **初期スコープ** = 核心 MVP 優先（P1〜P3）。汎用化（S3 gateway, P5）は後回し。
3. **書き込み範囲** = フル CRUD（list/get/put/delete + interrupt 投入）を最初から。
   ただし安全は既存の `SafeKeyValueStore` / `validate_safe_path` を必ず通す。

## 要望の対応表

**方針補正（2026-06-21 その2）**: interrupt 専用 UI は作らない。**汎用 CRUD ストレージ UI** が主であり、
interrupt は「1ディレクトリの読み書き」として汎用機能の上で扱う（特化エンドポイント不要）。加えて
**「ビューで重点的に扱いたいものを設定で標準化する」（pin/featured 設定）** を汎用機能として持つ。

| # | 要望 | 実装 |
|---|------|------|
| A | manystore IF の上に protocol 公開、FE が接続 | REST/WS が KeyValueStore を 1:1 で写す protocol |
| B | 複数コンテキスト（`.work` 等）公開 | config で context名→backend をマウント（`ArrayKeyValueStore` 的） |
| C | ディレクトリ監視 → WS ライブ通知 | local=watchdog(inotify) / 他=polling の Watcher 抽象 |
| D | UI→サーバへ更新依頼（書込） | フル CRUD（PUT/DELETE）。**interrupt 投入もこの汎用 PUT で実現**（専用 action 不要・必須） |
| E | **汎用 UI**（主目標） | 汎用ファイルブラウザ的 UI。interrupt はその上の 1 ディレクトリとして読み書き |
| G | **ビューの重点設定（pin/featured）** | config で「重点的に扱うパス/prefix」を宣言→UI が pin/強調/既定選択/並びで標準表示 |
| F | S3 gateway（二次・汎用化の別経路） | P5 の S3 gateway（別アダプタ・疎結合） |

## アーキテクチャ

```
ブラウザ FE ──WS/REST──▶ manystore-server (FastAPI) ──▶ manystore backend (local/s3/nats/…)
http_store(RW拡張) ─────▶ 同 protocol                   (Safe* で包む)
```

- 既存 read-only `http_store`（M018）の **対になるサーバ**。この REST/WS が「IF の上に公開する protocol」。
- protocol を一度定義すれば ①ブラウザ FE ②`http_store`(RW拡張) ③任意スクリプト の 3 クライアントが乗る。

## protocol（REST/WS ドラフト）

- `GET  /contexts` → マウント済み context 一覧
- `GET  /contexts/{ctx}/keys?prefix=&limit=` → キー一覧（KeyValueStore.list）
- `HEAD /contexts/{ctx}/objects/{key}` → exists
- `GET  /contexts/{ctx}/objects/{key}` → get（ストリーミング）
- `PUT  /contexts/{ctx}/objects/{key}` → put
- `DELETE /contexts/{ctx}/objects/{key}` → delete
- `POST /contexts/{ctx}/interrupt` {text, name?} → interrupt/ へ書込（E の最短経路）
- `WS   /contexts/{ctx}/events` → `{type: created|modified|deleted, key}` を push

→ KeyValueStore（put/get/list/exists/delete）と 1:1。サーバは薄い HTTP アダプタ。

## Watcher 抽象

- `Watcher` protocol（subscribe → AsyncIterator[Event]）。
- `LocalWatcher`: watchdog(inotify) で FS イベント→key イベントに翻訳。
- `PollingWatcher`: list の差分（mtime/size/hash）を interval ポーリング。s3/nats は native event 無しなのでこれ。

## config（context マウント定義 + ビュー重点設定）

```toml
# (B) マウント: context名 → backend
[contexts.work]
backend = "local"
root = ".work"
# [contexts.<name>] backend=... + backend固有パラメータ

# (G) ビューの重点設定: UI が「標準で重点的に扱う」対象を宣言（汎用）
[[views.featured]]
context = "work"
path = "skills/memory-bank/interrupt"  # 例: interrupt を pin（だが特化はしない）
label = "Interrupt"                     # 表示名（任意）
pin = true                              # サイドバー上部に固定
quick_write = true                      # その場でテキスト新規作成できる（汎用の「新規テキスト」）
# 既定で開く context / 並び順 / prefix フィルタ等もここで宣言可
default_context = "work"
```

→ interrupt は `quick_write=true` な featured パスの一例。UI 自体は interrupt を知らず、
config が「重点表示するパス」を与えるだけ＝**汎用 UI のまま** interrupt 投入が手早くできる。

## 段階計画

- **P1 サーバ MVP**: FastAPI で context マウント + フル CRUD REST + WS購読 + LocalWatcher。`curl`/`wscat` で確認。
- **P2 ビュー重点設定（featured/pin）**: config の `views.featured` を protocol で返し、`quick_write` 含む。
  interrupt 投入は「featured な local ディレクトリへの汎用 PUT」として成立（特化エンドポイント無し）。
- **P3 フロントエンド**: ビルド不要の最小 Web UI（FastAPI static + 素の WebSocket）＝**汎用ファイルブラウザ**。
  - 左: context 一覧 + featured(pin) + キーツリー / 右: ファイル閲覧・編集 / 新規テキスト作成。WS で自動更新。
- **P4 http_store RW 拡張**: サーバ protocol を喋る書込対応クライアント化（往復が閉じる）。
- **P5（任意・並行）S3 gateway**: 既存 S3 browser を汎用 UI に使えるアダプタ。watch は別系統。

## 技術選定（案）

- サーバ: **FastAPI + uvicorn**（manystore の async 一次方針と整合、WS ネイティブ）。
- 監視: **watchdog**（local/inotify）。
- FE: **ビルドレス**（素の HTML/JS）で MVP。重くなったら別途検討。
- 安全: 既存 `SafeKeyValueStore` で各 context を包む。既定 bind は localhost、token は任意で後付け。

## レイアウト（確定: 単一ディストリビューション + optional extras）

**決定の巻き戻し（2026-06-21 その3）**: 当初「別パッケージ」だったが、ユーザー選択で
**`manystore` 配下に 3 層のサブパッケージを足す一体型**に確定。重い依存は **optional extras
`manystore[server]` + 遅延 import**（既存 backend と同じ作法）で配布の軽さを維持＝本体は膨らまない。
import 名前空間が `manystore.{implement,server,client}` で統一される利点。uv workspace は使わない。

```
manystore/
  __init__.py / async_storage.py / backends/ …   # 既存コア（不変）
  implement/      # 実装層（backend非依存・HTTP不要で単体テスト可）
    protocol.py   #   protocol 型（dataclass。server/client 共有の契約。fastapi非依存）
    config.py     #   contexts マウント + views.featured（tomllib）
    service.py    #   protocol → KeyValueStore マッピング中核（StorageService）
    watcher.py    #   Watcher / PollingWatcher（+将来 LocalWatcher=watchdog）
  server/         # サーバ層（fastapi/uvicorn を遅延 import。extra: manystore[server]）
    app.py / routes.py / __main__.py / static/
  client/         # クライアント層（protocol を喋る Python SDK。http_store RW の母体）
    http_client.py
tests/
  test_storage.py / test_e2e_backends.py          # 既存（不変）
  ui/ test_implement.py / test_server.py / test_client.py
```

- 依存: `[project.optional-dependencies] server = ["fastapi","uvicorn","watchdog"]`。dev group にも入れてテスト可能に。
- 監視は MVP では **PollingWatcher**（純 stdlib・全 backend 対応・決定的＝テスト容易）。inotify(watchdog)
  ベースの LocalWatcher は最適化として後続。WS push 自体は polling 起点でも要件（ライブ通知）を満たす。

## 未決・後で詰める

- 認証（token/localhost 限定）の既定値。
- 大容量ファイルのストリーミング/レンジ取得。
- polling interval の既定とイベント重複排除。
- FE をビルドレス維持か（将来 Vite 等に移すか）。
