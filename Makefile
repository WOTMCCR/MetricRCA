export METRIC_RCA_DB_DSN ?= mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca
export METRIC_RCA_READONLY_DB_DSN ?= mysql+pymysql://metric_rca_reader:metric_rca_reader@127.0.0.1:3307/metric_rca
SEED ?= 20260606
SEED_PROFILE ?= regression
ALLOW_DESTRUCTIVE_SEED ?= false

.PHONY: up seed api ui eval eval-regression eval-blind eval-seed-sweep eval-mutation eval-memory-treatment eval-acceptance eval-stream eval-http eval-gaps test test-e2e

EVAL_ID ?=

BASE_URL ?= http://127.0.0.1:8000
HTTP_TIMEOUT ?= 600
HTTP_CONCURRENCY ?= $(or $(METRIC_RCA_EVAL_CONCURRENCY),1)

up:
	docker compose up -d mysql

seed:
	METRIC_RCA_DATA_SEED=$(SEED) METRIC_RCA_SEED_PROFILE=$(SEED_PROFILE) METRIC_RCA_ALLOW_DESTRUCTIVE_SEED=$(ALLOW_DESTRUCTIVE_SEED) python -m metric_rca.data.seed_data

api:
	uvicorn metric_rca.api.main:app --reload

ui:
	npm run dev --prefix frontend

eval:
	python -m metric_rca.evals.runner

eval-regression:
	METRIC_RCA_EVAL_SUITE=regression python -m metric_rca.evals.runner $(if $(EVAL_ID),--eval-id $(EVAL_ID),)

eval-blind:
	METRIC_RCA_EVAL_SUITE=blind python -m metric_rca.evals.runner

eval-seed-sweep:
	METRIC_RCA_EVAL_SUITE=seed-sweep python -m metric_rca.evals.runner

eval-mutation:
	METRIC_RCA_EVAL_SUITE=mutation python -m metric_rca.evals.runner

eval-memory-treatment:
	METRIC_RCA_EVAL_SUITE=memory-treatment python -m metric_rca.evals.runner

eval-acceptance:
	METRIC_RCA_EVAL_SUITE=acceptance python -m metric_rca.evals.runner

eval-stream:
	python -m metric_rca.evals.runner --stream $(if $(EVAL_ID),--eval-id $(EVAL_ID),)

eval-http:
	@test -n "$(PROVIDER)" || (echo "PROVIDER is required for eval-http" >&2; exit 2)
	@test -n "$(MODEL)" || (echo "MODEL is required for eval-http" >&2; exit 2)
	python -m metric_rca.evals.client --base-url $(BASE_URL) --provider $(PROVIDER) --model $(MODEL) --timeout $(HTTP_TIMEOUT) --concurrency $(HTTP_CONCURRENCY)

eval-gaps:
	python -m metric_rca.evals.gap_analyzer --eval-id $(EVAL_ID)

test:
	pytest -q

test-e2e:
	METRIC_RCA_E2E_SMOKE=1 pytest tests/test_e2e_smoke.py -v --timeout=120
