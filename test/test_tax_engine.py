import unittest
import sys
import types
from datetime import datetime, timezone

if "logtail" not in sys.modules:
    logtailStub = types.ModuleType("logtail")

    class _DummyLogtailHandler:
        def __init__(self, *args, **kwargs):
            pass

    logtailStub.LogtailHandler = _DummyLogtailHandler
    sys.modules["logtail"] = logtailStub

if "loguru" not in sys.modules:
    loguruStub = types.ModuleType("loguru")

    class _DummyLogger:
        def remove(self, *args, **kwargs):
            return None

        def add(self, *args, **kwargs):
            return None

        def info(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

    loguruStub.logger = _DummyLogger()
    sys.modules["loguru"] = loguruStub

from api.services.billing.taxEngine import computeTax


class TestTaxEngine(unittest.TestCase):
    def test_intra_state_tax_breakdown(self):
        result = computeTax(
            amountBeforeTax=10000,
            customerState="KA",
            invoiceTimestamp=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(result.igst, 0)
        self.assertGreater(result.cgst, 0)
        self.assertGreater(result.sgst, 0)
        self.assertEqual(result.total_amount, result.amount_before_tax + result.tax_amount)
        self.assertTrue(result.is_intra_state)

    def test_inter_state_tax_breakdown(self):
        result = computeTax(
            amountBeforeTax=10000,
            customerState="MH",
            invoiceTimestamp=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )

        self.assertGreater(result.igst, 0)
        self.assertEqual(result.cgst, 0)
        self.assertEqual(result.sgst, 0)
        self.assertEqual(result.total_amount, result.amount_before_tax + result.tax_amount)
        self.assertFalse(result.is_intra_state)


if __name__ == "__main__":
    unittest.main()
