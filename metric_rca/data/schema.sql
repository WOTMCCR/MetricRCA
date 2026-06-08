CREATE DATABASE IF NOT EXISTS metric_rca;
USE metric_rca;

SET GLOBAL max_execution_time = 3000;

CREATE TABLE dim_product (
  product_id    INT PRIMARY KEY,
  product_name  VARCHAR(128) NOT NULL,
  category      VARCHAR(64)  NOT NULL,
  price         DECIMAL(10,2) NOT NULL,
  KEY idx_category (category)
) ENGINE=InnoDB;

CREATE TABLE dim_user (
  user_id    INT PRIMARY KEY,
  reg_date   DATE NOT NULL,
  city       VARCHAR(64),
  KEY idx_reg_date (reg_date)
) ENGINE=InnoDB;

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

CREATE TABLE fact_inventory (
  business_date  DATE NOT NULL,
  product_id     INT NOT NULL,
  warehouse      VARCHAR(32) NOT NULL,
  stockout_hours DECIMAL(5,2) NOT NULL DEFAULT 0,
  avail_hours    DECIMAL(5,2) NOT NULL DEFAULT 24,
  PRIMARY KEY (business_date, product_id, warehouse)
) ENGINE=InnoDB;

CREATE TABLE fact_campaign (
  business_date DATE NOT NULL,
  campaign_id   INT NOT NULL,
  channel       VARCHAR(32) NOT NULL,
  spend         DECIMAL(12,2) NOT NULL,
  clicks        INT NOT NULL,
  impressions   INT NOT NULL,
  PRIMARY KEY (business_date, campaign_id)
) ENGINE=InnoDB;

CREATE TABLE fact_customer_ticket (
  ticket_id     BIGINT PRIMARY KEY,
  business_date DATE NOT NULL,
  product_id    INT NOT NULL,
  ticket_type   VARCHAR(32) NOT NULL,
  is_complaint  TINYINT NOT NULL DEFAULT 0,
  KEY idx_date_product (business_date, product_id)
) ENGINE=InnoDB;

CREATE TABLE metric_definition (
  metric_id    VARCHAR(32) PRIMARY KEY,
  display_name VARCHAR(64) NOT NULL,
  formula      VARCHAR(255) NOT NULL,
  numerator_sql_fragment VARCHAR(255),
  denominator_sql_fragment VARCHAR(255),
  higher_is_better TINYINT NOT NULL DEFAULT 1,
  source_table VARCHAR(64) NOT NULL,
  allowed_dimensions VARCHAR(255) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE anomaly_ground_truth (
  case_id          VARCHAR(64) PRIMARY KEY,
  business_date    DATE NOT NULL,
  metric_id        VARCHAR(32) NOT NULL,
  expected_anomaly TINYINT NOT NULL,
  root_cause_type  VARCHAR(64),
  dimension        VARCHAR(32),
  element          VARCHAR(64)
) ENGINE=InnoDB;

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

CREATE TABLE operation_task (
  task_id     VARCHAR(64) PRIMARY KEY,
  run_id      VARCHAR(64) NOT NULL,
  title       VARCHAR(255) NOT NULL,
  root_cause_type VARCHAR(64) NOT NULL,
  payload     JSON,
  created_at  DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE memory_record (
  memory_id  VARCHAR(64) PRIMARY KEY,
  layer      VARCHAR(16) NOT NULL,
  mem_key    VARCHAR(128) NOT NULL,
  payload    JSON NOT NULL,
  confidence DECIMAL(4,3) NOT NULL DEFAULT 0.500,
  source     VARCHAR(64) NOT NULL DEFAULT 'system',
  version    INT NOT NULL DEFAULT 1,
  ttl_days   INT,
  created_at DATETIME NOT NULL,
  KEY idx_layer_key (layer, mem_key)
) ENGINE=InnoDB;

CREATE TABLE eval_run (
  eval_id    VARCHAR(64) PRIMARY KEY,
  created_at DATETIME NOT NULL,
  summary    JSON NOT NULL
) ENGINE=InnoDB;

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

CREATE USER IF NOT EXISTS 'metric_rca_reader'@'%' IDENTIFIED BY 'metric_rca_reader';
GRANT SELECT ON metric_rca.* TO 'metric_rca_reader'@'%';
GRANT ALL PRIVILEGES ON metric_rca.* TO 'metric_rca_app'@'%';
FLUSH PRIVILEGES;
