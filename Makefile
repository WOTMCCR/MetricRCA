export METRIC_RCA_DB_DSN ?= mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca
export METRIC_RCA_READONLY_DB_DSN ?= mysql+pymysql://metric_rca_reader:metric_rca_reader@127.0.0.1:3307/metric_rca
SEED ?= 20260606

.PHONY: up seed api ui eval eval-stream eval-http eval-gaps test

EVAL_ID ?=

BASE_URL ?= http://127.0.0.1:8000
HTTP_TIMEOUT ?= 600
LOCAL_TRACING_ENV ?= LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false

up:
	docker compose up -d mysql

seed:
	METRIC_RCA_DATA_SEED=$(SEED) python -m metric_rca.data.seed_data

api:
	$(LOCAL_TRACING_ENV) uvicorn metric_rca.api.main:app --reload

ui:
	npm run dev --prefix frontend

eval:
	$(LOCAL_TRACING_ENV) python -m metric_rca.evals.runner

eval-stream:
	$(LOCAL_TRACING_ENV) python -m metric_rca.evals.runner --stream $(if $(EVAL_ID),--eval-id $(EVAL_ID),)

eval-http:
	@test -n "$(PROVIDER)" || (echo "PROVIDER is required for eval-http" >&2; exit 2)
	@test -n "$(MODEL)" || (echo "MODEL is required for eval-http" >&2; exit 2)
	$(LOCAL_TRACING_ENV) python -m metric_rca.evals.client --base-url $(BASE_URL) --provider $(PROVIDER) --model $(MODEL) --timeout $(HTTP_TIMEOUT)

eval-gaps:
	$(LOCAL_TRACING_ENV) python -m metric_rca.evals.gap_analyzer --eval-id $(EVAL_ID)

test:
	pytest -q
