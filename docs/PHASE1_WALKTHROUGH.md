# Phase 1 代码详解：数据地基 + 取数安全闭环

> 对应提交 `1a00551`（`feat: implement Phase 1 (data + guardrails) and add docs-compliance gate`）。
> 本文按「文件提交/构建顺序」+「功能闭环顺序」讲解 Phase 1 代码：每个模块**是什么 / 为什么 / 作用 / 解决了文档里的什么要求**。
> 验证状态：代码已提交，但需 MySQL 在线（`make up && make seed && make test`）才能跑通 DB 相关测试；本文描述实现意图，未断言全绿。

---

## 0. 一句话总览

Phase 1 落地的是 MetricRCA 唯一合法取数链路：

```
QuerySpec ──build──► SQLRenderer ──render──► SQLGuard ──guard──► MetricRepository ──► MySQL(只读账号)
   (受控意图)         (确定性模板SQL)        (sqlglot AST校验)      (只执行通过的Plan)        │
                                                                                            └──► sql_audit (旁路审计)
```

它不实现任何 Agent / 图 / API / UI（那是 Phase >1）。它要解决的核心文档要求是：
**事实只来自数据库 + 确定性代码，绝不让 LLM 写 SQL；任何取数都必须可校验、可审计、不可旁路。**
对应 `COMPLIANCE_MATRIX.md` 第 1–10、26 行，以及 `IMPLEMENTATION_CONTRACT.md` 的多条 P0 红线。

---

## 1. 文件提交清单（按构建/依赖顺序）

| 顺序 | 文件 | 一句话职责 | 矩阵行 |
|---|---|---|---|
| 1 | `pyproject.toml` | 只声明 P1 依赖（含 sqlglot），固定版本 | 1 |
| 2 | `docker-compose.yml` | MySQL 8.x（init schema.sql + healthcheck） | 3 |
| 3 | `Makefile` | `up` / `seed` / `test`（`eval` 仅占位、不假成功） | 2 |
| 4 | `metric_rca/config/settings.py` | 统一类型化配置（DSN、阈值、运行上限、LLM/Memory 开关） | 26 |
| 5 | `metric_rca/domain/enums.py` | 4 个枚举（指标/维度/根因/证据判定） | 6 |
| 6 | `metric_rca/domain/models.py` | 全部 Pydantic v2 契约模型（`extra="forbid"`） | 6/7 |
| 7 | `metric_rca/data/schema.sql` | 17 张业务+系统表 + 只读 DB 账号 | 4 |
| 8 | `metric_rca/data/anomaly_injection.py` | 确定性异常注入函数（按 target_date 触发） | 5 |
| 9 | `metric_rca/data/seed_data.py` | 固定种子 60 天数据 + 5 ground truth，幂等 | 5 |
| 10 | `metric_rca/guardrails/query_spec.py` | `build_query_spec`：白名单校验的意图构造器 | 7 |
| 11 | `metric_rca/guardrails/renderer.py` | QuerySpec → 参数化 SQL + sha256 + 渲染器签名 | 8 |
| 12 | `metric_rca/guardrails/sql_guard.py` | sqlglot AST 守卫 + 守卫签名 | 9 |
| 13 | `metric_rca/repositories/metric_repository.py` | 只执行通过的 Plan，写 sql_audit，系统表持久化 | 10 |
| 14 | `metric_rca/evals/runner.py` | Phase >1 占位（打印 NOT IMPLEMENTED，退出码 1） | 23 |
| 15 | `tests/*` | 11 个测试，按「能否击穿 shortcut」编写 | 各行 |

> 构建顺序≈依赖顺序≈功能闭环顺序：配置 → 模型 → 库表 → 数据 → 取数四件套（spec/renderer/guard/repo）。

---

## 2. 逐层详解（功能闭环顺序）

### 2.1 配置层 — `config/settings.py`

- **是什么**：基于 `pydantic-settings` 的 `Settings`，`env_prefix="METRIC_RCA_"`、`extra="forbid"`。集中所有口径：`db_dsn` / `readonly_db_dsn`（均 `min_length=1`、无默认值）、`tz=Asia/Tokyo`、`business_today=2026-06-06`、`target_date=2026-06-05`、`thresh_pct=0.15`、`z_thresh=2.0`、`max_steps=8` 等上限、`statement_timeout_ms=3000`、`llm_enabled/required/provider`、`memory_enabled/required`。
- **为什么**：文档要求阈值/上限不能是散落的魔法数，且「不做默认 provider/config 替换」。
- **作用**：
  - DSN 必填（缺失即报错）→ 杜绝「连不上就静默走默认/内存库」。
  - `model_validator`：当 `llm_required=True` 但无 `llm_provider` 时，**构造期**直接抛 `LLM_REQUIRED_UNAVAILABLE`。这把「要求 LLM 却不可用」的 Zero-Fallback 红线前移到了配置层。
  - `lru_cache` 的 `get_settings()` 保证全局单例口径一致。
- **解决文档问题**：矩阵第 26 行；契约「no default provider/config substitution」「llm required 不可用→typed error」。

### 2.2 领域模型 — `domain/enums.py` + `domain/models.py`

- **是什么**：`enums.py` 提供 `MetricId / DimensionId / RootCauseType / EvidenceVerdict`，与设计文档 §2 完全一致。`models.py` 用一个 `StrictModel(BaseModel, extra="forbid")` 基类派生**全部**契约模型：`TimeRange / MetricDefinition / Dimension / Baseline / QuerySpec / SQLPlan / Evidence / Observation / RootCauseCandidate / AgentAction / ReflectionIssue / ReflectionResult / MemoryRecord / EvalCase / TraceStep / AgentRun`。
- **为什么**：`extra="forbid"` 让任何非法字段在入口即被 `ValidationError` 拦截——这是「在边界拦住脏数据」的硬要求，也是测试能击穿 shortcut 的前提。
- **作用（重点是 `QuerySpec`）**：它是「替代任意 Text-to-SQL」的核心契约，带三重校验：
  1. `metric_id` 必须 ∈ `PHASE1_METRICS`（= 全部指标去掉 1 个月才做的 `campaign_roi`）；
  2. `group_by` ≤2 且全部 ∈ 维度白名单；
  3. `model_validator` 再用 `METRIC_ALLOWED_DIMENSIONS` 做「指标↔维度」交叉白名单（如 `complaint_rate` 只能按 `category/product` 下钻）。
  - `SQLPlan` 比文档多了 `renderer_signature / guard_signature` 两个字段——见 §4 工程加固。
- **解决文档问题**：矩阵第 6、7 行；契约「required modules contain real responsibilities」「QuerySpec 是唯一受控查询契约」。

### 2.3 库表 — `data/schema.sql`

- **是什么**：建库 + 17 张表（6 业务事实/维表 + `metric_definition` + `anomaly_ground_truth` + 7 系统表 `agent_run/trace_step/evidence/sql_audit/operation_task/memory_record/eval_run/eval_case_result`）。`business_date` 用 `DATE`，系统时间戳用 `DATETIME`，`trace_step/evidence/...` 用 `JSON` 列。表名严格用 `fact_customer_ticket`。
- **为什么**：文档明确「不再保留时间不足可省略的系统表」，`memory_record/eval_*` 是可测试契约。
- **作用**：末尾创建**只读账号**：`metric_rca_reader` 仅 `GRANT SELECT`，应用账号 `metric_rca_app` 才有全权限。这就是文档说的「DB 层第二道防线」。同时设 `max_execution_time=3000` 防慢查。
- **解决文档问题**：矩阵第 3、4 行；契约「只读账号」「不得丢系统表」「fact_customer_ticket 不混用 fact_ticket」。

### 2.4 异常注入 + 种子 — `data/anomaly_injection.py` + `data/seed_data.py`

- **是什么**：
  - `anomaly_injection.py`：一组**纯函数**，仅当 `business_date == TARGET_DATE(2026-06-05)` 时返回异常倍率——paid_ads 流量/投放骤降（uv×0.38、spend×0.30）、mobile 支付人数×0.55、electronics 缺货 15.5h、商品 1 退款率 0.75。把「异常长什么样」与「数据怎么生成」解耦。
  - `seed_data.py`：`SEED=20260606`，`HISTORY_DAYS=60`，起点 = `TARGET_DATE - 59 天`；先 `DELETE` 全表再插入（**幂等重建**）；生成周内效应、季节性、渠道/类目/设备分布、投放/库存/投诉退款；写 4 条 `metric_definition`（gmv/net_gmv/pay_cvr/refund_rate）与 5 条 ground truth。带 `_wait_for_mysql` 重试。
- **为什么**：没有固定种子 + ground truth，就无法做可复现、不靠人读的 eval。
- **作用**：为后续异常检测/归因提供「有真因可对照」的确定性数据底座。
- **解决文档问题**：矩阵第 5 行；契约「Eval reads anomaly_ground_truth」「seed 幂等」。
- **设计修正（已解决早期自相矛盾）**：`gmv_no_anomaly`（期望 GMV 无异常）现放在 **2026-06-04**（`GMV_NO_ANOMALY_DATE`），与 4 个异常 case 所在的 `TARGET_DATE=2026-06-05` 分离——因此"同一 gmv 指标既异常又无异常"的冲突已不存在，异常注入函数也不再需要补偿项。仍建议 Phase 2 接入异常检测后用 eval 实测确认各 case 判定正确。

### 2.5 取数四件套（功能闭环核心）

#### ① `guardrails/query_spec.py` — 意图构造器
- **是什么**：`build_query_spec(...)`，失败抛带 `code` 的 `QuerySpecError`（`QUERY_SPEC_INVALID` / `DIMENSION_NOT_ALLOWED`），并把 Pydantic 的 `ValidationError` 统一包成 typed error。
- **作用**：把「外部意图」收敛成已校验的 `QuerySpec`，错误码与文档 §1.4 边界表一致。
- **解决**：矩阵第 7 行；「问题→受控意图」而非「问题→自由 SQL」。

#### ② `guardrails/renderer.py` — 确定性渲染器
- **是什么**：`METRIC_TEMPLATES` 给每个指标定义事实表 + 聚合表达式（如 gmv = `SUM(CASE WHEN is_paid=1 ...)`，pay_cvr = `SUM(pay_user_cnt)/NULLIF(SUM(uv),0)`）；`DIMENSION_COLUMNS` + `JOIN_BY_FACT_AND_DIMENSION` 把维度确定性映射到列与**白名单 INNER JOIN**。
- **作用**：
  - 渲染参数化 SQL：日期用 `business_date BETWEEN :start_date AND :end_date`，过滤值用 `:filter_<dim>` 占位（防注入）；`category` 维度才拼 `INNER JOIN dim_product ...`；强制 `LIMIT`；算出 `sql_hash = sha256(sql)`。
  - **JOIN 只可能来自渲染器**——LLM/调用方无法注入 join。
- **解决**：矩阵第 8 行；文档「LLM 不写 SQL」「JOIN 由元数据确定性派生」「强制 LIMIT/日期」。

#### ③ `guardrails/sql_guard.py` — sqlglot AST 守卫
- **是什么**：`guard_sql()` 用 `sqlglot.parse(read="mysql")` 解析为 AST，逐条判定后返回带 `guard_status` 的新 `SQLPlan`。
- **作用（拒绝项）**：多语句、非 `SELECT`、`Insert/Update/Delete/Drop/Create/Alter/Command`、`Anonymous` 自定义函数、`SELECT *`、CTE(`With`)、子查询/派生表、非白名单表/列、缺事实表、非渲染器 JOIN、缺 `business_date` 过滤（且必须是 `Literal/Placeholder` 绑定值）、缺 `LIMIT`；并先校验 `sql_hash` 与 `sql` 一致。**正则只是辅助，判定全在 AST**——所以混淆注释里的 `DROP`、派生表都拦得住。
- **解决**：矩阵第 9 行（P0：正则冒充 AST）；文档 §11 守卫清单全量。

#### ④ `repositories/metric_repository.py` — 只读执行 + 审计
- **是什么**：`MetricRepository`，`from_settings` 同时建 `readonly_engine`（只读账号，跑业务查询）与 `audit_engine`（应用账号，写系统表）。
- **作用**：`execute_plan` 执行前做**五重门禁**：guard_status=passed → sql_hash 匹配 → 渲染器签名有效 → 守卫签名有效 → **再 guard 一次复核**；执行时 `SET SESSION max_execution_time`，`text()` 参数化；无论成功/失败都写 `sql_audit`（含 hash/guard_status/guard_errors/row_count/latency）。`close()` dispose 两个 engine（保证 `-W error::ResourceWarning` 下无告警）。还提供 `create_agent_run/trace_step/evidence/...` 系统表写入，连内部 `system_table_counts` 都做列白名单。
- **解决**：矩阵第 10 行；契约「只执行通过的 Plan，不可旁路」「写 sql_audit」「参数化、只读边界、连接生命周期干净」。

### 2.6 `evals/runner.py`
- **是什么/作用**：故意只 `print("NOT IMPLEMENTED (Phase >1)")` 并 `return 1`（非零退出）。
- **解决**：矩阵第 2/23 行的反 shortcut 约束——「eval 占位可存在，但绝不假成功」。

---

## 3. 端到端串讲：一次「按渠道看 GMV」取数

1. 调用方 `build_query_spec(metric_id="gmv", start/end, group_by=["channel"])` → 通过三重白名单 → 得到 `QuerySpec`。
2. `SQLRenderer.render(spec)` → `SELECT fact_order.channel AS channel, SUM(CASE WHEN is_paid=1 ...) AS metric_value FROM fact_order WHERE business_date BETWEEN :start_date AND :end_date GROUP BY ... ORDER BY metric_value DESC LIMIT 1000`，附 `sql_hash` 与 `renderer_signature`。
3. `guard_sql(plan)` → AST 全项通过 → 盖上 `guard_signature`，`guard_status="passed"`。
4. `repo.execute_plan(plan, run_id=...)` → 五重门禁通过 → 只读账号执行 → 返回 `QueryResult` → 旁路写一条 `sql_audit`。
5. 任一环节不合规：要么 `QuerySpecError`，要么 `guard_status="rejected"`（带 errors），要么 `execute_plan` 抛 `SQL_GUARD_REJECTED/SQL_PLAN_INVALID`——**没有任何静默继续的支路**。

---

## 4. 超出文档的工程加固：HMAC「双盖章」溯源

`renderer.py` 与 `sql_guard.py` 各持一个**进程内随机密钥**，对 `sql_hash` 做 HMAC：渲染器产出时盖 `renderer_signature`，守卫通过时盖 `guard_signature`；`execute_plan` 必须两枚签名都验过才执行。

- **为什么**：文档只说「SQLGuard 不可旁路」。但若仅检查 `plan.guard_status == "passed"`，攻击者/未来代码可以**手工构造**一个 `SQLPlan(guard_status="passed")` 直接喂给 repo。双盖章把「这条 SQL 确实由本进程的渲染器生成、并由本进程的守卫放行」变成密码学事实——手工伪造的 Plan 因没有有效签名而被拒。
- **作用**：把契约里的 P0「No SQLGuard bypass / dangerous_sql 不可执行」做成了**结构上不可绕过**，而不是约定俗成。这也是后续 `test_sql_guard_rejection_cannot_bypass_renderer` 能真正击穿 shortcut 的底层保证。

---

## 5. 解决了文档/契约里的哪些硬要求（映射）

| 文档/契约要求 | Phase 1 落点 |
|---|---|
| 唯一取数路径 QuerySpec→Renderer→Guard→Repository | §2.5 四件套 + repo 五重门禁 |
| LLM 不写 SQL、不旁路证据 | 渲染器模板化 + 守卫 AST + 双盖章 |
| SQLGuard 用 sqlglot AST 非正则（P0） | `sql_guard.py` 全 AST 判定 |
| 不做默认 provider/config 替换；llm required 不可用→typed error | `settings.py` 必填 DSN + 构造期校验 |
| 只读账号第二道防线 | `schema.sql` 建 `metric_rca_reader` + repo readonly_engine |
| 17 表齐全、business_date DATE、fact_customer_ticket | `schema.sql` |
| 种子幂等 + 5 ground truth + 固定口径 | `seed_data.py` + `anomaly_injection.py` |
| 所有 SQL 落 sql_audit | repo 成功/失败均 `_write_audit` |
| eval 不假成功 | `evals/runner.py` 占位非零退出 |
| 模型 `extra="forbid"`、Pydantic v2 | `StrictModel` 基类 |

---

## 6. 边界与下一步（Phase 2）

- Phase 1 **不含**：异常检测/归因服务、工具层、LangGraph 图与节点、Reflection、Memory、API、UI、eval scorer。
- 进入 Phase 2 前建议：`make up && make seed && make test` 跑通，并在异常检测接入后用一次 eval 实测确认 5 个 case 判定正确（含 6-04 的 no_anomaly）；然后用只读 Codex review 对照 `COMPLIANCE_MATRIX.md` 第 1–10、26 行复核一次「是否有偷换概念」。
