-- Emergency rollback for 20260620_01_decouple_evidence_identity.sql.
-- Use only before any evidence reference longer than 64 characters is written.
-- MySQL DDL implicitly commits. Back up the table before applying.

-- Both preflight results must be 0 before rollback.
SELECT COUNT(*) AS overlong_evidence_id_count
FROM evidence
WHERE CHAR_LENGTH(evidence_id) > 64;

SELECT COUNT(*) AS noncanonical_identity_count
FROM evidence
WHERE evidence_id <> CONCAT(run_id, ':', alias);

ALTER TABLE evidence
  DROP INDEX uq_evidence_run_alias,
  DROP INDEX uq_evidence_id,
  DROP PRIMARY KEY,
  DROP COLUMN evidence_pk,
  DROP COLUMN alias,
  MODIFY COLUMN evidence_id VARCHAR(64) NOT NULL,
  ADD PRIMARY KEY (evidence_id);
