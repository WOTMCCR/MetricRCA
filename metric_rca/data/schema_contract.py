"""Code-level limits mirrored by the persisted MetricRCA schema.

`tests/test_evidence_schema_contract.py` prevents these values from drifting
from `schema.sql` and the production migration.
"""

AGENT_RUN_ID_MAX_LENGTH = 64
EVIDENCE_ALIAS_MAX_LENGTH = 96
EVIDENCE_REFERENCE_MAX_LENGTH = 192
