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

.PHONY: format format-check lint test test-all check ui e2e-up e2e-down

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

# テスト（内ループ既定＝fast のみ。slow=実 backend/ネットワーク/ポーリング待ちを除外＝R13）
test:
	uv run pytest -m "not slow"

# 全テスト（CI / 明示時。slow も含めて回す）
test-all:
	uv run pytest

# 一括検証（format 確認 + fast test）＝内ループの「検証緑」判定
check: format-check test

# 実 backend E2E の起動＋S3 identity 登録（これで s3-path / nats ケースが走る）
e2e-up:
	docker compose up -d nats seaweedfs
	@echo "SeaweedFS の起動待ち..."; sleep 4
	echo 's3.configure -access_key $(E2E_S3_ACCESS_KEY) -secret_key $(E2E_S3_SECRET_KEY) -user manystore -actions Read,Write,List,Tagging,Admin -apply' | docker compose exec -T seaweedfs weed shell

# 実 backend の停止
e2e-down:
	docker compose down
