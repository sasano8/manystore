---
from: dotfiles
role: supervisor
type: instruction
priority: high
date: 2026-06-24
---

## 指示

テストの実行時間を [[unit-quality]] **R13（テスト実行時間の規律）** に沿って整える。
高速にテストを回せること自体が品質。次の 2 つをやる。

### 1. 軽重分離（fast / slow）

- 待ち支配（実バックエンド起動・ネットワーク・sleep/ポーリング）のテストに `@pytest.mark.slow` を付ける。
- `pyproject.toml [tool.pytest.ini_options]` に
  `markers = ["slow: 実バックエンド/ネットワーク/ポーリング待ちを伴う重いテスト"]` を登録（unknown-mark 警告回避）。
- Makefile を分離（R5）:
  - `make test`     → `uv run pytest -m "not slow"`（内ループ既定＝fast のみ）
  - `make test-all` → `uv run pytest`（全部。CI / 明示時）
  - `check:` は `format-check test` のまま（内ループは fast に乗る）。
  - ※ `addopts` でなく make ターゲット側で絞る（test-all で上書きできるように）。
- slow 候補（実測で setup/teardown・call が長い＝要確認。最終判断は worker 文脈で）:
  - `tests/test_e2e_backends.py`（s3-path / nats の e2e）
  - `tests/ui/test_gateway_s3client.py`（real_s3_client_* の実バックエンド setup/teardown）
  - `tests/ui/test_combined.py`（s3_client_roundtrip 系）
  - `tests/ui/test_implement.py::test_polling_watcher_detects_changes`（ポーリング sleep）

### 2. 未整備依存の早期 skip（タイムアウト待ちにしない）

- `tests/test_e2e_backends.py:177` の `s3-virtual` は **TimeoutError まで待ってから SKIP** している
  （`SKIPPED [1] tests/test_e2e_backends.py:177: s3-virtual: 環境/認証 未整備 → TimeoutError:`）。
  この「待ってから skip」がそのまま内ループの遅延になる＝R13 のアンチパターン。
- **接続を試みる前に軽い可用性チェックで早期 skip** に変える（`pytest.skip` / `@pytest.mark.skipif`）。
  本接続・本認証の前に env/認証の有無や軽いプローブで判定し、未整備なら即 skip。timeout 経由にしない。

## 背景 / 受け入れ条件

- 背景（supervisor 側 profiling の実測）: 125 テストで壁時計 ~15s だが **CPU 使用率 15%** ＝
  計算ではなく待ち（バックエンド setup/teardown・sleep/ポーリング・timeout）を直列に積んでいるのが主因。
  テスト増加でこの待ちの総和が伸びる構造。最遅の個別テストでも 0.44s で、重いテストは無い。
- 判断の正本は [[unit-quality]] R13（軽重分離・未整備は早期 skip・高速フィードバック）。
- 受け入れ条件:
  - `make test`（fast）が体感で大幅短縮（slow を除外して走る）。
  - `make test-all` で全テストが従来どおり通る（落ちない・スキップ条件は維持）。
  - `s3-virtual` 等の未整備依存が **timeout を待たずに** skip される（再現: 未整備環境で `make test-all` が即 skip）。
  - `slow` マーカが pyproject に登録され警告が出ない。
- 注: これは worker 自身の flow→unit-quality の開発内ループで実施・検証すること（R13 を自己点検で参照）。
