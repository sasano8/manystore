# manystore — 開発タスク
# format は uvx で ruff のバージョンを固定して実行する（環境差を排除）。

# ruff のピン留めバージョン（更新時はここだけ変える）
RUFF_VERSION := 0.15.18
PYLINT_VERSION := latest

# lint/format/test の対象
SRC := manystore tests

# 実 backend E2E 用の開発 S3 identity（SeaweedFS に登録する固定鍵。tests の既定もこれ）
E2E_S3_ACCESS_KEY := manystore
E2E_S3_SECRET_KEY := manystoresecret123

# slow（実 backend）テストの per-test 目標時間（秒）。超過＝ハング扱いで stack を吐いて落とす
# （pytest-timeout・必要以上の待機を防ぐ backstop）。正規の最遅は ~10s なので CI ばらつき込みで 60s。
TEST_HEAVY_TIMEOUT := 60

.PHONY: format format-check lint pylint test test-heavy test-benchmark test-all cov cov-html check grep-todo ui e2e-up e2e-down conformance-docs docs docs-serve

# ストレージ UI / サーバを開発設定で起動（既定 http://127.0.0.1:8000）。
# 既定ストレージは .cache/manystore_dev（使い捨て・起動時に自動作成）。PORT=xxxx で上書き可。
UI_CONFIG := examples/manystore-ui.dev.toml
PORT := 8000
ui:
	uv run python -m manystore.serving.server --config $(UI_CONFIG) --port $(PORT)

# コード整形（自動修正）
format:
	uvx ruff@$(RUFF_VERSION) format $(SRC)
	uvx ruff@$(RUFF_VERSION) check --fix $(SRC)

# 整形確認のみ（CI 向け・書き換えない）
format-check:
	uvx ruff@$(RUFF_VERSION) format --check $(SRC)
	uvx ruff@$(RUFF_VERSION) check $(SRC)

# lint のみ
lint:
	uvx ruff@$(RUFF_VERSION) check $(SRC)
	# pyright basedpyright
	# jscpd

pylint:
	uvx pylint@$(PYLINT_VERSION) manystore --enable=duplicate-code

# テスト 4 段（R13）。内ループ既定＝fast（slow=実 backend/ネットワーク/ポーリング待ち、
# benchmark=性能計測 を除外）。slow/benchmark はマーカーで分離。
test:
	uv run pytest -svx -m "not slow and not benchmark"

# 重いテスト（実 backend/ネットワーク/ポーリング待ち）。先に `make e2e-up` で backend を起動する。
# `MANYSTORE_E2E_REQUIRED=1` を焼き込む＝backend 未起動なら番兵が**赤**（silent skip で緑を素通り
# させない・local==CI）。docker 無しで slow を見たいだけなら `uv run pytest -m slow` を直接叩く。
# `--timeout` で per-test の目標時間を設ける＝詰まりは stack を吐いて落とし、必要以上に待たない。
test-heavy:
	MANYSTORE_E2E_REQUIRED=1 uv run pytest -m "slow" --timeout=$(TEST_HEAVY_TIMEOUT)

# ベンチマーク（環境差で揺れる＝gate にせず情報収集に留める。該当無しなら exit 5 でも可）。
test-benchmark:
	uv run pytest -m "benchmark"

# 全テスト（CI / 明示時。slow・benchmark も含めて回す）
test-all:
	uv run pytest

# カバレッジ計測（fast テストで未到達行を term に出す）。happy-path 偏重の穴を定量把握する（M059）。
cov:
	uv run pytest -m "not slow and not benchmark" --cov=manystore --cov-report=term-missing

# カバレッジを HTML で出す（`htmlcov/index.html` をブラウザで開く。行単位の未到達を可視化）。
cov-html:
	uv run pytest -m "not slow and not benchmark" --cov=manystore --cov-report=html
	@echo "open htmlcov/index.html （WSL: explorer.exe htmlcov/index.html）"

cov-html-show:
	@python -m http.server 8000 --directory htmlcov

# 一括検証（format 確認 + fast test）＝内ループの「検証緑」判定
check: format-check test

# 作業マーカー（TODO/FIXME/HACK）を file:line で拾う（R16）。書式は `# TODO(<backlog-id>): what`。
# 非ヒットでも CI を割らないよう exit 0。
grep-todo:
	@grep -rnE 'TODO|FIXME|HACK' $(SRC) --include='*.py' || true

# conformance 結果を docs の spec 表へ出力（メソッド × 実装の Implemented/Not）。
# 接続不要・決定的。docs/kv_spec.md / docs/file_storage_spec.md を再生成する。
conformance-docs:
	uv run python -m manystore.tools.conformancer

# docs サイト（MkDocs Material）をビルド。先に conformance spec を再生成して常に最新化。
# 出力は site/（CI の Pages デプロイがこれを公開）。--strict で警告を失敗にする。
docs: conformance-docs
	uv run --group docs mkdocs build --strict

# ローカルでプレビュー（http://127.0.0.1:8000）。spec を再生成してから serve。
docs-serve: conformance-docs
	uv run --group docs mkdocs serve

# 実 backend E2E の起動＋S3 identity 登録（これで nats / s3（seaweedfs・minio）ケースが走る）。
# SeaweedFS と MinIO の両方を立て、conformance の s3 実装マトリクスを実機検証する。
e2e-up:
	docker compose up -d nats seaweedfs minio
	@echo "SeaweedFS の起動待ち..."; sleep 4
	echo 's3.configure -access_key $(E2E_S3_ACCESS_KEY) -secret_key $(E2E_S3_SECRET_KEY) -user manystore -actions Read,Write,List,Tagging,Admin -apply' | docker compose exec -T seaweedfs weed shell
	@echo "MinIO の起動待ち..."; \
	  for i in $$(seq 1 30); do \
	    curl -sf http://localhost:9000/minio/health/live >/dev/null 2>&1 && break || sleep 1; \
	  done

# 実 backend の停止
e2e-down:
	docker compose down
