import sys
import types
import unittest

for _name in ("logtail", "loguru", "redis"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)
if not hasattr(sys.modules["logtail"], "LogtailHandler"):
    sys.modules["logtail"].LogtailHandler = lambda *a, **k: None
if not hasattr(sys.modules["loguru"], "logger"):
    class _L:
        def __getattr__(self, _):
            return lambda *a, **k: None
    sys.modules["loguru"].logger = _L()
if not hasattr(sys.modules["redis"], "Redis"):
    sys.modules["redis"].Redis = lambda *a, **k: None
if not hasattr(sys.modules["redis"], "ConnectionPool"):
    sys.modules["redis"].ConnectionPool = type("ConnectionPool", (), {})

from api.services.credits.creditConfig import (
    TOKEN_TO_CREDIT_RATIO,
    getTokenQuotaForPlan,
    getOperationMinimum,
)


class TestCreditConfigMonthly(unittest.TestCase):
    def test_ratio_is_ten_thousand(self):
        self.assertEqual(TOKEN_TO_CREDIT_RATIO, 10000)

    def test_paid_quota_is_ten_million_tokens_per_domain(self):
        self.assertEqual(getTokenQuotaForPlan("pro", 1), 10000000)
        self.assertEqual(getTokenQuotaForPlan("annual", 1), 10000000)

    def test_paid_quota_scales_with_domain_count(self):
        self.assertEqual(getTokenQuotaForPlan("pro", 4), 40000000)
        self.assertEqual(getTokenQuotaForPlan("annual", 2), 20000000)

    def test_domain_count_defaults_to_one(self):
        self.assertEqual(getTokenQuotaForPlan("pro"), 10000000)

    def test_zero_or_negative_domain_count_clamps_to_one(self):
        # A malformed count must never zero out a paying subscriber's balance.
        self.assertEqual(getTokenQuotaForPlan("pro", 0), 10000000)
        self.assertEqual(getTokenQuotaForPlan("pro", -3), 10000000)

    def test_free_quota_is_three_hundred_credits_and_does_not_scale(self):
        self.assertEqual(getTokenQuotaForPlan("free"), 3000000)
        self.assertEqual(getTokenQuotaForPlan("free", 4), 3000000)

    def test_none_and_unknown_plans_get_zero(self):
        self.assertEqual(getTokenQuotaForPlan("none"), 0)
        self.assertEqual(getTokenQuotaForPlan("mystery"), 0)
        self.assertEqual(getTokenQuotaForPlan("mystery", 4), 0)

    def test_quotas_are_whole_credit_multiples(self):
        for plan in ("free", "pro", "annual"):
            self.assertEqual(getTokenQuotaForPlan(plan) % TOKEN_TO_CREDIT_RATIO, 0)

    def test_operation_minimums_are_token_counts(self):
        self.assertEqual(getOperationMinimum("reporting_query"), 5000)
        self.assertEqual(getOperationMinimum("transformation_message"), 10000)
        self.assertEqual(getOperationMinimum("speech_to_text"), 2000)

    def test_unknown_operation_falls_back_to_default(self):
        self.assertEqual(getOperationMinimum("unlisted_operation"), 1000)


if __name__ == "__main__":
    unittest.main()
