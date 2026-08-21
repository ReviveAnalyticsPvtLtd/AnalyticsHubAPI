import unittest


class TestActiveTopupWindow(unittest.TestCase):
    def test_single_untouched_purchase_reports_zero_used(self):
        from api.services.credits.creditPresentation import deriveActiveTopupWindow

        self.assertEqual(
            deriveActiveTopupWindow(5_000_000, [5_000_000]),
            {"total": 5_000_000, "used": 0},
        )

    def test_single_partially_used_purchase_keeps_original_total(self):
        from api.services.credits.creditPresentation import deriveActiveTopupWindow

        self.assertEqual(
            deriveActiveTopupWindow(1_500_000, [5_000_000]),
            {"total": 5_000_000, "used": 3_500_000},
        )

    def test_second_purchase_extends_active_total_without_resetting_usage(self):
        from api.services.credits.creditPresentation import deriveActiveTopupWindow

        self.assertEqual(
            deriveActiveTopupWindow(6_500_000, [5_000_000, 5_000_000]),
            {"total": 10_000_000, "used": 3_500_000},
        )

    def test_exhausted_oldest_purchase_drops_out_of_active_window(self):
        from api.services.credits.creditPresentation import deriveActiveTopupWindow

        self.assertEqual(
            deriveActiveTopupWindow(5_000_000, [5_000_000, 5_000_000]),
            {"total": 5_000_000, "used": 0},
        )

    def test_zero_available_balance_has_no_active_lots(self):
        from api.services.credits.creditPresentation import deriveActiveTopupWindow

        self.assertEqual(
            deriveActiveTopupWindow(0, [5_000_000]),
            {"total": 0, "used": 0},
        )

    def test_invalid_lots_are_ignored(self):
        from api.services.credits.creditPresentation import deriveActiveTopupWindow

        self.assertEqual(
            deriveActiveTopupWindow(1_500_000, [None, -10, "bad", 5_000_000]),
            {"total": 5_000_000, "used": 3_500_000},
        )

    def test_incomplete_history_falls_back_to_available_balance(self):
        from api.services.credits.creditPresentation import deriveActiveTopupWindow

        self.assertEqual(
            deriveActiveTopupWindow(6_500_000, [5_000_000]),
            {"total": 6_500_000, "used": 0},
        )


class TestCreditView(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "monthlyTokenQuota": 10_000_000,
            "usedTokens": 2_500_000,
            "topupTotalTokens": 5_000_000,
            "topupUsedTokens": 1_500_000,
        }

    def test_credit_only_view_uses_nested_monthly_and_topup_buckets(self):
        from api.services.credits.creditPresentation import buildCreditView

        self.assertEqual(
            buildCreditView(self.snapshot),
            {
                "monthlyCredits": {
                    "total": 1000.0,
                    "used": 250.0,
                    "percentageUsed": 25.0,
                },
                "topupCredits": {
                    "total": 500.0,
                    "used": 150.0,
                },
            },
        )

    def test_full_view_adds_matching_token_buckets(self):
        from api.services.credits.creditPresentation import buildCreditView

        view = buildCreditView(self.snapshot, includeTokens=True)

        self.assertEqual(
            view["monthlyTokens"],
            {"total": 10_000_000, "used": 2_500_000},
        )
        self.assertEqual(
            view["topupTokens"],
            {"total": 5_000_000, "used": 1_500_000},
        )
        self.assertEqual(view["monthlyCredits"]["percentageUsed"], 25.0)
        self.assertNotIn("percentageUsed", view["topupCredits"])

    def test_view_does_not_copy_legacy_combined_fields(self):
        from api.services.credits.creditPresentation import buildCreditView

        snapshot = {
            **self.snapshot,
            "remainingCredits": 1100.0,
            "remainingTokens": 11_000_000,
            "usagePercentage": 25.0,
        }
        view = buildCreditView(snapshot, includeTokens=True)

        self.assertNotIn("remainingCredits", view)
        self.assertNotIn("remainingTokens", view)
        self.assertNotIn("usagePercentage", view)

    def test_empty_snapshot_returns_zeroed_buckets(self):
        from api.services.credits.creditPresentation import buildCreditView

        self.assertEqual(
            buildCreditView({}, includeTokens=True),
            {
                "monthlyCredits": {
                    "total": 0.0,
                    "used": 0.0,
                    "percentageUsed": 0.0,
                },
                "topupCredits": {"total": 0.0, "used": 0.0},
                "monthlyTokens": {"total": 0, "used": 0},
                "topupTokens": {"total": 0, "used": 0},
            },
        )

    def test_profile_projection_is_trimmed_to_credit_ui_fields(self):
        from api.services.credits.creditPresentation import buildProfileCreditView

        view = buildProfileCreditView({
            **self.snapshot,
            "planTier": "pro",
            "periodStart": "2026-08-01T00:00:00+00:00",
            "periodEnd": "2026-09-01T00:00:00+00:00",
            "lastResetAt": "2026-08-01T00:00:00+00:00",
            "initialized": True,
        })

        self.assertEqual(
            set(view),
            {"monthlyCredits", "topupCredits", "periodEnd", "initialized"},
        )
        self.assertEqual(view["periodEnd"], "2026-09-01T00:00:00+00:00")
        self.assertTrue(view["initialized"])

    def test_balance_projection_has_full_metadata_and_nested_buckets(self):
        from api.services.credits.creditPresentation import buildCreditBalanceView

        view = buildCreditBalanceView({
            **self.snapshot,
            "planTier": "pro",
            "periodStart": "2026-08-01T00:00:00+00:00",
            "periodEnd": "2026-09-01T00:00:00+00:00",
            "lastResetAt": "2026-08-01T00:00:00+00:00",
            "initialized": True,
            "remainingCredits": 1100.0,
            "remainingTokens": 11_000_000,
        })

        self.assertEqual(
            set(view),
            {
                "planTier", "monthlyTokens", "topupTokens", "monthlyCredits",
                "topupCredits", "periodStart", "periodEnd", "lastResetAt",
                "initialized",
            },
        )
        self.assertEqual(view["planTier"], "pro")
        self.assertNotIn("remainingCredits", view)
        self.assertNotIn("remainingTokens", view)


if __name__ == "__main__":
    unittest.main()
