export METRIC_RCA_DB_DSN ?= mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca
export METRIC_RCA_READONLY_DB_DSN ?= mysql+pymysql://metric_rca_reader:metric_rca_reader@127.0.0.1:3307/metric_rca

.PHONY: up seed eval test

up:
	docker compose up -d mysql

seed:
	python -m metric_rca.data.seed_data

eval:
	python -m metric_rca.evals.runner

test:
	pytest -q
