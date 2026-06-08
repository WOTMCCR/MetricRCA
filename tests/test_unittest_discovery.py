from __future__ import annotations

import unittest

from metric_rca.guardrails.sql_guard import guard_sql


class ResourceWarningCleanSmokeTest(unittest.TestCase):
    def test_unittest_discovery_runs_without_resource_warning(self) -> None:
        plan = guard_sql(
            "SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1"
        )
        self.assertEqual(plan.guard_status, "passed", plan.guard_errors)


if __name__ == "__main__":
    unittest.main()
