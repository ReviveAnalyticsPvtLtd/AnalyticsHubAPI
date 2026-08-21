import unittest
from datetime import datetime, timezone

from api.services.credits.creditMath import (
    rollMonthly,
    nextPeriodEnd,
    tokensToCredits,
)


class TestCreditMath(unittest.TestCase):
    def test_roll_monthly_single_period(self):
        pe = datetime(2026, 7, 1, tzinfo=timezone.utc)
        now = datetime(2026, 7, 2, tzinfo=timezone.utc)
        ps2, pe2 = rollMonthly(pe, now)
        self.assertEqual(ps2, datetime(2026, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(pe2, datetime(2026, 8, 1, tzinfo=timezone.utc))

    def test_roll_monthly_multiple_missed_periods(self):
        pe = datetime(2026, 5, 1, tzinfo=timezone.utc)
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        ps2, pe2 = rollMonthly(pe, now)
        self.assertEqual(ps2, datetime(2026, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(pe2, datetime(2026, 8, 1, tzinfo=timezone.utc))

    def test_next_period_end_advances_one_calendar_month(self):
        self.assertEqual(
            nextPeriodEnd(datetime(2026, 7, 1, tzinfo=timezone.utc)),
            datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

    def test_next_period_end_clamps_short_month(self):
        self.assertEqual(
            nextPeriodEnd(datetime(2026, 1, 31, tzinfo=timezone.utc)),
            datetime(2026, 2, 28, tzinfo=timezone.utc),
        )

    def test_tokens_to_credits_rounds_to_two_places(self):
        self.assertEqual(tokensToCredits(10000000, 10000), 1000.0)
        self.assertEqual(tokensToCredits(4800, 10000), 0.48)
        self.assertEqual(tokensToCredits(1234, 10000), 0.12)

    def test_tokens_to_credits_guards_bad_inputs(self):
        self.assertEqual(tokensToCredits(5000, 0), 0.0)
        self.assertEqual(tokensToCredits(-5, 10000), 0.0)
        self.assertEqual(tokensToCredits(0, 10000), 0.0)


if __name__ == "__main__":
    unittest.main()
