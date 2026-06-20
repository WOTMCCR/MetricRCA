-- Decouple the physical evidence primary key from the compatibility reference.
-- MySQL DDL implicitly commits. Back up the table before applying this migration.
-- Apply once to an existing MetricRCA database before deploying the matching code.

-- Every preflight result must be 0.
SELECT COUNT(*) AS invalid_evidence_identity_count
FROM evidence
WHERE run_id LIKE '%:%'
   OR LEFT(evidence_id, CHAR_LENGTH(run_id) + 1) <> CONCAT(run_id, ':')
   OR CHAR_LENGTH(evidence_id) <= CHAR_LENGTH(run_id) + 1;

SELECT COUNT(*) AS overlong_derived_alias_count
FROM evidence
WHERE CHAR_LENGTH(SUBSTRING(evidence_id, CHAR_LENGTH(run_id) + 2)) > 96;

SELECT COUNT(*) AS duplicate_run_alias_count
FROM (
  SELECT run_id,
         SUBSTRING(evidence_id, CHAR_LENGTH(run_id) + 2) AS derived_alias,
         COUNT(*) AS duplicate_count
  FROM evidence
  GROUP BY run_id, derived_alias
  HAVING COUNT(*) > 1
) AS duplicates;

ALTER TABLE evidence
  MODIFY COLUMN evidence_id VARCHAR(192) NOT NULL,
  ADD COLUMN alias VARCHAR(96) NULL AFTER run_id;

UPDATE evidence
SET alias = SUBSTRING(evidence_id, CHAR_LENGTH(run_id) + 2)
WHERE alias IS NULL;

ALTER TABLE evidence
  MODIFY COLUMN alias VARCHAR(96) NOT NULL,
  DROP PRIMARY KEY,
  ADD COLUMN evidence_pk BIGINT UNSIGNED NOT NULL AUTO_INCREMENT FIRST,
  ADD PRIMARY KEY (evidence_pk),
  ADD UNIQUE KEY uq_evidence_id (evidence_id),
  ADD UNIQUE KEY uq_evidence_run_alias (run_id, alias);
