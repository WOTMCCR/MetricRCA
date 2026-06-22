export METRIC_RCA_DB_DSN ?= mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca
export METRIC_RCA_READONLY_DB_DSN ?= mysql+pymysql://metric_rca_reader:metric_rca_reader@127.0.0.1:3307/metric_rca
SEED ?= 20260606
SEED_PROFILE ?= regression
ALLOW_DESTRUCTIVE_SEED ?= false

EVAL_ID ?=
EVAL_OUTPUT_DIR ?= eval_out
BASE_URL ?= http://127.0.0.1:8000
HTTP_TIMEOUT ?= 600
HTTP_CONCURRENCY ?= $(or $(METRIC_RCA_EVAL_CONCURRENCY),1)

PTV_OUTPUT_ROOT ?= eval_out/ptv
PTV_CYCLE_ID ?=
PTV_ROUND ?=
PTV_EVAL_ID ?=
PTV_EVAL_CODE_COMMIT ?=
PTV_FIX_COMMIT ?=
PTV_POST_EVAL_REVIEW_FIX_COMMIT ?=
PTV_CONFIRMATION_OF_ROUND ?=
PTV_TOTAL_CASES ?= 46
PTV_MAX_ROUNDS ?= 25
PTV_PREDICTION_COMMAND ?=
PTV_ANALYST_COMMAND ?=
PTV_SELECTED_FIX_CATEGORY ?=
PTV_SELECTED_LAYER ?=
PTV_CONTROLLER_JUSTIFICATION ?=
PTV_REVERT_DECISION ?=
PTV_PRIVATE_GROUND_TRUTH ?=

GRPO_CYCLE_DIR ?= $(PTV_OUTPUT_ROOT)/$(PTV_CYCLE_ID)
GRPO_OUTPUT_DIR ?=
GRPO_FROM_ROUND ?=
GRPO_TO_ROUND ?=

SCENARIO_CATALOG ?= metric_rca/data/scenarios/catalog.yaml
SCENARIO_SET ?= metric_rca/data/scenarios/phase_c_full.yaml
SCENARIO_OUTPUT_DIR ?= eval_out/generated_data
SCENARIO_PROFILE ?= scenario

.PHONY: up seed api ui eval eval-regression eval-blind eval-seed-sweep eval-mutation eval-memory-treatment eval-acceptance eval-stream eval-http eval-gaps test test-e2e ptv-cycle ptv-prepare ptv-round ptv-analyze ptv-finalize ptv-verify grpo-export scenario-generate

up:
	docker compose up -d mysql

seed:
	@if [ "$(SEED_PROFILE)" = "scenario" ]; then \
		python -m metric_rca.data.scenario_seed \
			--catalog $(SCENARIO_CATALOG) --scenario-set $(SCENARIO_SET) \
			--output-dir $(SCENARIO_OUTPUT_DIR) --seed $(SEED) --profile $(SCENARIO_PROFILE); \
	else \
		METRIC_RCA_DATA_SEED=$(SEED) METRIC_RCA_SEED_PROFILE=$(SEED_PROFILE) \
		METRIC_RCA_ALLOW_DESTRUCTIVE_SEED=$(ALLOW_DESTRUCTIVE_SEED) \
		python -m metric_rca.data.seed_data; \
	fi

api:
	uvicorn metric_rca.api.main:app --reload

ui:
	npm run dev --prefix frontend

eval:
	python -m metric_rca.evals.runner

eval-regression:
	METRIC_RCA_EVAL_SUITE=regression python -m metric_rca.evals.runner$(if $(EVAL_ID), --eval-id $(EVAL_ID))

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
	python -m metric_rca.evals.runner --stream --output-dir $(EVAL_OUTPUT_DIR) $(if $(EVAL_ID),--eval-id $(EVAL_ID),)

eval-http:
	@test -n "$(PROVIDER)" || (echo "PROVIDER is required for eval-http" >&2; exit 2)
	@test -n "$(MODEL)" || (echo "MODEL is required for eval-http" >&2; exit 2)
	python -m metric_rca.evals.client --base-url $(BASE_URL) --provider $(PROVIDER) --model $(MODEL) --timeout $(HTTP_TIMEOUT) --concurrency $(HTTP_CONCURRENCY)

eval-gaps:
	python -m metric_rca.evals.gap_analyzer --output-dir $(EVAL_OUTPUT_DIR) --eval-id $(EVAL_ID)

ptv-cycle:
	python -m metric_rca.evals.ptv_runner --output-root $(PTV_OUTPUT_ROOT) init-cycle \
		$(if $(PTV_CYCLE_ID),--cycle-id $(PTV_CYCLE_ID),) \
		--total-cases $(PTV_TOTAL_CASES) --max-rounds $(PTV_MAX_ROUNDS)

ptv-prepare:
	@test -n "$(PTV_CYCLE_ID)" || (echo "PTV_CYCLE_ID is required" >&2; exit 2)
	@test -n "$(PTV_ROUND)" || (echo "PTV_ROUND is required" >&2; exit 2)
	@test -n "$(PTV_EVAL_ID)" || (echo "PTV_EVAL_ID is required" >&2; exit 2)
	@test -n "$(PTV_EVAL_CODE_COMMIT)" || (echo "PTV_EVAL_CODE_COMMIT is required" >&2; exit 2)
	python -m metric_rca.evals.ptv_runner --output-root $(PTV_OUTPUT_ROOT) prepare-round \
		--cycle-id $(PTV_CYCLE_ID) --round $(PTV_ROUND) --eval-id $(PTV_EVAL_ID) \
		--eval-code-commit $(PTV_EVAL_CODE_COMMIT) \
		$(if $(PTV_FIX_COMMIT),--fix-commit $(PTV_FIX_COMMIT),) \
		$(if $(PTV_POST_EVAL_REVIEW_FIX_COMMIT),--post-eval-review-fix-commit $(PTV_POST_EVAL_REVIEW_FIX_COMMIT),) \
		$(if $(PTV_CONFIRMATION_OF_ROUND),--confirmation-of-round $(PTV_CONFIRMATION_OF_ROUND),)

ptv-round:
	@test -n "$(PTV_CYCLE_ID)" || (echo "PTV_CYCLE_ID is required" >&2; exit 2)
	@test -n "$(PTV_ROUND)" || (echo "PTV_ROUND is required" >&2; exit 2)
	@test -n "$(PTV_EVAL_ID)" || (echo "PTV_EVAL_ID is required" >&2; exit 2)
	@test -n "$(PTV_EVAL_CODE_COMMIT)" || (echo "PTV_EVAL_CODE_COMMIT is required" >&2; exit 2)
	@test -n "$(PTV_PREDICTION_COMMAND)" || (echo "PTV_PREDICTION_COMMAND is required" >&2; exit 2)
	@test -n "$(PTV_ANALYST_COMMAND)" || (echo "PTV_ANALYST_COMMAND is required" >&2; exit 2)
	@test -n "$(PTV_CONTROLLER_JUSTIFICATION)" || (echo "PTV_CONTROLLER_JUSTIFICATION is required" >&2; exit 2)
	python -m metric_rca.evals.ptv_runner --output-root $(PTV_OUTPUT_ROOT) run-round \
		--cycle-id $(PTV_CYCLE_ID) --round $(PTV_ROUND) --eval-id $(PTV_EVAL_ID) \
		--eval-code-commit $(PTV_EVAL_CODE_COMMIT) \
		$(if $(PTV_FIX_COMMIT),--fix-commit $(PTV_FIX_COMMIT),) \
		$(if $(PTV_POST_EVAL_REVIEW_FIX_COMMIT),--post-eval-review-fix-commit $(PTV_POST_EVAL_REVIEW_FIX_COMMIT),) \
		$(if $(PTV_CONFIRMATION_OF_ROUND),--confirmation-of-round $(PTV_CONFIRMATION_OF_ROUND),) \
		--prediction-command "$(PTV_PREDICTION_COMMAND)" \
		--eval-command "python -m metric_rca.evals.runner --stream --allow-threshold-failure --output-dir $(PTV_OUTPUT_ROOT)/$(PTV_CYCLE_ID)/round-$(shell printf '%02d' $(PTV_ROUND)) --eval-id $(PTV_EVAL_ID)" \
		--analyst-command "$(PTV_ANALYST_COMMAND)" \
		$(if $(PTV_SELECTED_FIX_CATEGORY),--selected-fix-category $(PTV_SELECTED_FIX_CATEGORY),) \
		$(if $(PTV_SELECTED_LAYER),--selected-layer $(PTV_SELECTED_LAYER),) \
		--controller-justification "$(PTV_CONTROLLER_JUSTIFICATION)" \
		$(if $(PTV_REVERT_DECISION),--revert-decision $(PTV_REVERT_DECISION),) \
		$(if $(PTV_PRIVATE_GROUND_TRUTH),--private-ground-truth $(PTV_PRIVATE_GROUND_TRUTH),)

ptv-analyze:
	python -m metric_rca.evals.ptv_runner --output-root $(PTV_OUTPUT_ROOT) analyze \
		--cycle-id $(PTV_CYCLE_ID) --round $(PTV_ROUND) --eval-id $(PTV_EVAL_ID)

ptv-finalize:
	@test -n "$(PTV_CONTROLLER_JUSTIFICATION)" || (echo "PTV_CONTROLLER_JUSTIFICATION is required" >&2; exit 2)
	python -m metric_rca.evals.ptv_runner --output-root $(PTV_OUTPUT_ROOT) finalize \
		--cycle-id $(PTV_CYCLE_ID) --round $(PTV_ROUND) \
		$(if $(PTV_SELECTED_FIX_CATEGORY),--selected-fix-category $(PTV_SELECTED_FIX_CATEGORY),) \
		$(if $(PTV_SELECTED_LAYER),--selected-layer $(PTV_SELECTED_LAYER),) \
		--controller-justification "$(PTV_CONTROLLER_JUSTIFICATION)" \
		$(if $(PTV_REVERT_DECISION),--revert-decision $(PTV_REVERT_DECISION),) \
		$(if $(PTV_PRIVATE_GROUND_TRUTH),--private-ground-truth $(PTV_PRIVATE_GROUND_TRUTH),) \
		$(if $(PTV_CONFIRMATION_OF_ROUND),--confirmation-round,)

ptv-verify:
	python -m metric_rca.evals.ptv_runner --output-root $(PTV_OUTPUT_ROOT) verify \
		--cycle-id $(PTV_CYCLE_ID) --round $(PTV_ROUND)

grpo-export:
	@test -d "$(GRPO_CYCLE_DIR)" || (echo "GRPO_CYCLE_DIR must be an existing PTV cycle directory" >&2; exit 2)
	python -m metric_rca.evals.grpo_exporter \
		--cycle-dir $(GRPO_CYCLE_DIR) --repo-root . \
		$(if $(GRPO_OUTPUT_DIR),--output-dir $(GRPO_OUTPUT_DIR),) \
		$(if $(GRPO_FROM_ROUND),--from-round $(GRPO_FROM_ROUND),) \
		$(if $(GRPO_TO_ROUND),--to-round $(GRPO_TO_ROUND),)

scenario-generate:
	python -m metric_rca.data.scenario_seed \
		--catalog $(SCENARIO_CATALOG) --scenario-set $(SCENARIO_SET) \
		--output-dir $(SCENARIO_OUTPUT_DIR) --seed $(SEED) --profile $(SCENARIO_PROFILE)

test:
	pytest -q

test-e2e:
	METRIC_RCA_E2E_SMOKE=1 pytest tests/test_e2e_smoke.py -v --timeout=120
