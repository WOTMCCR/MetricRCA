-- MetricRCA 数据库 DDL（MySQL 8.x）。
-- 约定：业务事实表的 business_date 用 DATE（Asia/Tokyo 业务本地日）；系统表时间戳用 DATETIME（UTC）。
-- 共 17 张表：6 业务事实/维表 + metric_definition + anomaly_ground_truth + 7 系统表。
-- 末尾创建只读账号 metric_rca_reader（仅 SELECT），作为执行层的"第二道防线"。
-- 对应 docs/MetricRCA.md §9；docs/COMPLIANCE_MATRIX.md 第 3、4 行。
CREATE DATABASE IF NOT EXISTS metric_rca;
USE metric_rca;

-- 全局语句超时（毫秒），防慢查；repo 还会在会话级再设一次。
SET GLOBAL max_execution_time = 3000;

-- ========== 业务维度表 ==========
-- 商品维：category 是跨表下钻维度（渲染器通过白名单 JOIN 关联）。
CREATE TABLE dim_product (
  product_id    INT PRIMARY KEY,
  product_name  VARCHAR(128) NOT NULL,
  category      VARCHAR(64)  NOT NULL,
  price         DECIMAL(10,2) NOT NULL,
  KEY idx_category (category)
) ENGINE=InnoDB;

-- 用户维（MVP 暂不直接参与指标，仅维度完整性）。
CREATE TABLE dim_user (
  user_id    INT PRIMARY KEY,
  reg_date   DATE NOT NULL,
  city       VARCHAR(64),
  KEY idx_reg_date (reg_date)
) ENGINE=InnoDB;

-- ========== 业务事实表 ==========
-- 订单事实：gmv / net_gmv / aov / refund_rate 的来源；索引覆盖按日 + 按日×渠道/商品下钻。
CREATE TABLE fact_order (
  order_id      BIGINT PRIMARY KEY,
  business_date DATE NOT NULL,
  user_id       INT NOT NULL,
  product_id    INT NOT NULL,
  channel       VARCHAR(32) NOT NULL,
  device        VARCHAR(16) NOT NULL,
  order_amount  DECIMAL(10,2) NOT NULL,
  is_paid       TINYINT NOT NULL DEFAULT 0,
  is_refunded   TINYINT NOT NULL DEFAULT 0,
  refund_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
  KEY idx_date (business_date),
  KEY idx_date_channel (business_date, channel),
  KEY idx_date_product (business_date, product_id)
) ENGINE=InnoDB;

-- 流量漏斗事实：uv / pay_cvr 的来源（GMV = UV × PAY_CVR × AOV 分解依赖此表）。
CREATE TABLE fact_traffic (
  business_date DATE NOT NULL,
  channel       VARCHAR(32) NOT NULL,
  device        VARCHAR(16) NOT NULL,
  product_id    INT NOT NULL,
  uv            INT NOT NULL,
  pv            INT NOT NULL,
  add_cart_cnt  INT NOT NULL,
  pay_user_cnt  INT NOT NULL,
  PRIMARY KEY (business_date, channel, device, product_id)
) ENGINE=InnoDB;

-- 库存事实：stockout_rate 来源；缺货归因（stockout）的信号面。
CREATE TABLE fact_inventory (
  business_date  DATE NOT NULL,
  product_id     INT NOT NULL,
  warehouse      VARCHAR(32) NOT NULL,
  stockout_hours DECIMAL(5,2) NOT NULL DEFAULT 0,
  avail_hours    DECIMAL(5,2) NOT NULL DEFAULT 24,
  PRIMARY KEY (business_date, product_id, warehouse)
) ENGINE=InnoDB;

-- 投放事实：campaign_traffic_drop 归因的信号面（spend / clicks 骤降）。
CREATE TABLE fact_campaign (
  business_date DATE NOT NULL,
  campaign_id   INT NOT NULL,
  channel       VARCHAR(32) NOT NULL,
  spend         DECIMAL(12,2) NOT NULL,
  clicks        INT NOT NULL,
  impressions   INT NOT NULL,
  PRIMARY KEY (business_date, campaign_id)
) ENGINE=InnoDB;

-- 客诉/质量事实：complaint_rate 来源；质量问题归因的信号面。统一表名 fact_customer_ticket。
CREATE TABLE fact_customer_ticket (
  ticket_id     BIGINT PRIMARY KEY,
  business_date DATE NOT NULL,
  product_id    INT NOT NULL,
  ticket_type   VARCHAR(32) NOT NULL,
  is_complaint  TINYINT NOT NULL DEFAULT 0,
  KEY idx_date_product (business_date, product_id)
) ENGINE=InnoDB;

-- ========== 指标定义 + Ground Truth ==========
-- 指标口径库（公式 / 分子分母 / 来源表 / 可下钻维度 JSON）。
CREATE TABLE metric_definition (
  metric_id    VARCHAR(32) PRIMARY KEY,
  display_name VARCHAR(64) NOT NULL,
  formula      VARCHAR(255) NOT NULL,
  numerator_sql_fragment VARCHAR(255),
  denominator_sql_fragment VARCHAR(255),
  higher_is_better TINYINT NOT NULL DEFAULT 1,
  source_table VARCHAR(64) NOT NULL,
  allowed_dimensions VARCHAR(255) NOT NULL  -- JSON 数组
) ENGINE=InnoDB;

-- 评估真因：eval 据此逐 case 判定 anomaly / top1 / top3（不靠人读）。
CREATE TABLE anomaly_ground_truth (
  case_id          VARCHAR(64) PRIMARY KEY,
  business_date    DATE NOT NULL,
  metric_id        VARCHAR(32) NOT NULL,
  expected_anomaly TINYINT NOT NULL,
  root_cause_type  VARCHAR(64),
  dimension        VARCHAR(32),
  element          VARCHAR(64)
) ENGINE=InnoDB;

-- ========== Agent 系统表 ==========
-- 一次 RCA 运行的根记录（状态机：running→succeeded/no_anomaly/failed）。
CREATE TABLE agent_run (
  run_id      VARCHAR(64) PRIMARY KEY,
  question    VARCHAR(255) NOT NULL,
  metric_id   VARCHAR(32),
  target_date DATE NOT NULL,
  status      VARCHAR(16) NOT NULL,
  error_code  VARCHAR(48),
  created_at  DATETIME NOT NULL,
  finished_at DATETIME,
  KEY idx_status (status)
) ENGINE=InnoDB;

-- 可观测性：每个节点/步骤一条 span（输入/输出摘要、错误码、时延）。
CREATE TABLE trace_step (
  step_id    VARCHAR(64) PRIMARY KEY,
  run_id     VARCHAR(64) NOT NULL,
  seq        INT NOT NULL,
  node       VARCHAR(48) NOT NULL,
  action     VARCHAR(48),
  input_summary  JSON,
  output_summary JSON,
  error_code VARCHAR(48),
  latency_ms INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  KEY idx_run (run_id, seq)
) ENGINE=InnoDB;

-- 证据：每次取数的结构化快照（数值来源），结论必须绑定当前 run 的证据。
CREATE TABLE evidence (
  evidence_id   VARCHAR(64) PRIMARY KEY,
  run_id        VARCHAR(64) NOT NULL,
  query_spec    JSON NOT NULL,
  sql_text      TEXT NOT NULL,
  sql_hash      CHAR(64) NOT NULL,
  guard_status  VARCHAR(16) NOT NULL,
  result_summary JSON NOT NULL,
  data_source   VARCHAR(128) NOT NULL,
  created_at    DATETIME NOT NULL,
  KEY idx_run (run_id),
  KEY idx_hash (sql_hash)
) ENGINE=InnoDB;

-- SQL 审计：每条执行的 SQL（含被拒/失败）都落一行，便于安全复盘。
CREATE TABLE sql_audit (
  audit_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id       VARCHAR(64) NOT NULL,
  sql_text     TEXT NOT NULL,
  sql_hash     CHAR(64) NOT NULL,
  guard_status VARCHAR(16) NOT NULL,
  guard_errors JSON,
  row_count    INT,
  latency_ms   INT,
  created_at   DATETIME NOT NULL,
  KEY idx_run (run_id)
) ENGINE=InnoDB;

-- 运营任务：仅 confirmed/likely 主因生成；no_anomaly 不建任务。
CREATE TABLE operation_task (
  task_id     VARCHAR(64) PRIMARY KEY,
  run_id      VARCHAR(64) NOT NULL,
  title       VARCHAR(255) NOT NULL,
  root_cause_type VARCHAR(64) NOT NULL,
  payload     JSON,
  created_at  DATETIME NOT NULL
) ENGINE=InnoDB;

-- 记忆：带 confidence/source/version/ttl_days 的污染控制字段；记忆不得直接成为结论。
CREATE TABLE memory_record (
  memory_id  VARCHAR(64) PRIMARY KEY,
  layer      VARCHAR(16) NOT NULL,   -- case/semantic/episodic/reflection
  mem_key    VARCHAR(128) NOT NULL,
  payload    JSON NOT NULL,
  confidence DECIMAL(4,3) NOT NULL DEFAULT 0.500,
  source     VARCHAR(64) NOT NULL DEFAULT 'system',
  version    INT NOT NULL DEFAULT 1,
  ttl_days   INT,
  created_at DATETIME NOT NULL,
  KEY idx_layer_key (layer, mem_key)
) ENGINE=InnoDB;

-- ========== Eval 结果表 ==========
-- 一次 eval 运行的汇总（summary JSON 含 case_total/dangerous_sql_blocked/no_anomaly_correct 等）。
CREATE TABLE eval_run (
  eval_id    VARCHAR(64) PRIMARY KEY,
  created_at DATETIME NOT NULL,
  summary    JSON NOT NULL
) ENGINE=InnoDB;

-- 逐 case 评分明细。
CREATE TABLE eval_case_result (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  eval_id    VARCHAR(64) NOT NULL,
  case_id    VARCHAR(64) NOT NULL,
  intent_ok  TINYINT, anomaly_ok TINYINT,
  top1_ok    TINYINT, top3_ok TINYINT,
  evidence_coverage DECIMAL(4,3),
  sql_safe   TINYINT, reflection_repair_ok TINYINT,
  detail     JSON,
  KEY idx_eval (eval_id)
) ENGINE=InnoDB;

-- ========== 账号与权限（DB 层第二道防线）==========
-- 只读账号：仅 SELECT，供 repo 的 readonly_engine 执行业务查询。
CREATE USER IF NOT EXISTS 'metric_rca_reader'@'%' IDENTIFIED BY 'metric_rca_reader';
GRANT SELECT ON metric_rca.* TO 'metric_rca_reader'@'%';
-- 应用账号：全权限，供 seed 与系统表写入（audit_engine）使用。
GRANT ALL PRIVILEGES ON metric_rca.* TO 'metric_rca_app'@'%';
FLUSH PRIVILEGES;
