export METRIC_RCA_DB_DSN ?= mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca
export METRIC_RCA_READONLY_DB_DSN ?= mysql+pymysql://metric_rca_reader:metric_rca_reader@127.0.0.1:3307/metric_rca
SEED ?= 20260606

.PHONY: up seed api ui eval test

up:
	docker compose up -d mysql

seed:
	METRIC_RCA_DATA_SEED=$(SEED) uv run python -m metric_rca.data.seed_data

api:
	uv run uvicorn metric_rca.api.main:app --reload

ui:
	npm run dev --prefix frontend

eval:
	uv run python -m metric_rca.evals.runner

test:
	uv run pytest -q
