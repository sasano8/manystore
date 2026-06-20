# manystore — 開発タスク
# format は uvx で ruff のバージョンを固定して実行する（環境差を排除）。

# ruff のピン留めバージョン（更新時はここだけ変える）
RUFF_VERSION := 0.15.18

# lint/format/test の対象
SRC := manystore tests

.PHONY: format format-check lint test check

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

# テスト
test:
	uv run pytest

# 一括検証（format 確認 + test）
check: format-check test
