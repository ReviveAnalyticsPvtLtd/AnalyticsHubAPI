import sys
import types
import unittest


def _installStubs():
    """
    Stubs only `utils.logger`, whose logtail dependency is not installed here.

    `utils.exceptionHandler` is deliberately left alone: it imports cleanly, and
    shadowing it hides names other test modules import from the real one.
    """
    loggerModule = types.ModuleType("utils.logger")
    loggerModule.logger = types.SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
    )
    sys.modules["utils.logger"] = loggerModule


_installStubs()

from nubrix.components.dashboardPayloadBuilder import (  # noqa: E402
    DashboardPayloadBuilder,
    estimateTokens,
)

WIDGET_CODE = '''
def panelChart(**kwargs):
    return None
panelChart(
    projectId = "p1",
    chartType = "bar",
    xAxis = "region",
    yAxis = "revenue",
    aggregationMetric = "sum",
    dataSourceName = "Sales",
    tablesUsed = "sales_2024"
)
'''


def _context(widgets, metadata=None):
    return {
        "dashboard": {"pageId": "page_1", "pageName": "Overview", "widgetCount": len(widgets)},
        "chartData": widgets,
        "metadata": metadata or {},
        "domainContext": {},
        "statisticalSummary": {"widgetCount": len(widgets), "perWidget": {}},
    }


def _barWidget(ref="W1", rowCount=4):
    return {
        "ref": ref,
        "id": f"id-{ref}",
        "title": "Revenue by Region",
        "chartType": "bar",
        "xLabels": "region",
        "generatedCode": WIDGET_CODE,
        "data": {
            "labels": [f"region{index}" for index in range(rowCount)],
            "datasets": [{"label": "revenue", "data": list(range(rowCount))}],
        },
    }


def _cardWidget(ref="W0"):
    return {"ref": ref, "title": "Total Revenue", "chartType": "card", "label": "sum of revenue", "data": 41234.5}


class SectionAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.builder = DashboardPayloadBuilder()

    def test_payload_names_the_page_and_widget_count(self):
        payload = self.builder.build(_context([_barWidget()]))

        self.assertIn("## dashboard", payload)
        self.assertIn("Overview", payload)

    def test_cards_are_rendered_in_their_own_section_with_values(self):
        payload = self.builder.build(_context([_cardWidget(), _barWidget()]))

        self.assertIn("## kpi_cards", payload)
        self.assertIn("41234.5", payload)
        self.assertIn("Total Revenue", payload)

    def test_widget_block_carries_reference_title_type_and_csv_rows(self):
        payload = self.builder.build(_context([_barWidget()]))

        self.assertIn("### W1", payload)
        self.assertIn("Revenue by Region", payload)
        self.assertIn("[bar]", payload)
        self.assertIn("region,revenue", payload)
        self.assertIn("region0,0", payload)

    def test_widget_block_carries_provenance_from_generated_code(self):
        payload = self.builder.build(_context([_barWidget()]))

        self.assertIn("source: sum(revenue) by region from sales_2024", payload)

    def test_widget_signals_are_inlined_under_the_widget(self):
        context = _context([_barWidget()])
        context["statisticalSummary"]["perWidget"]["W1"] = {
            "concentration": {"top3Share": 0.9, "herfindahlIndex": 0.4, "numGroups": 4},
        }

        payload = self.builder.build(context)

        self.assertIn("signals: top3Share=0.9", payload)

    def test_payload_never_contains_raw_json_braces_from_chart_data(self):
        payload = self.builder.build(_context([_barWidget()]))

        self.assertNotIn('"datasets"', payload)


class SchemaPruningTests(unittest.TestCase):
    def setUp(self):
        self.builder = DashboardPayloadBuilder()
        self.metadata = {
            "sales_2024": {
                "description": "Sales fact table",
                "columns": [
                    {"name": "region", "description": "Sales region"},
                    {"name": "revenue", "description": "Net revenue"},
                ],
            },
            "support_tickets": {
                "description": "Support tickets",
                "columns": [
                    {"name": "ticket_id", "description": "Ticket identifier"},
                    {"name": "csat", "description": "Satisfaction score"},
                ],
            },
        }

    def test_referenced_tables_keep_full_column_descriptions(self):
        payload = self.builder.build(_context([_barWidget()], self.metadata))

        self.assertIn("Net revenue", payload)

    def test_unreferenced_tables_are_reduced_to_column_names(self):
        payload = self.builder.build(_context([_barWidget()], self.metadata))

        self.assertIn("support_tickets", payload)
        self.assertIn("ticket_id", payload)
        self.assertNotIn("Satisfaction score", payload)


class BudgetDegradationTests(unittest.TestCase):
    def test_large_dashboard_payload_stays_within_the_token_budget(self):
        builder = DashboardPayloadBuilder(fullRowLimit=40, compactRowLimit=5, tokenBudget=1200)
        widgets = [_barWidget(f"W{index}", rowCount=300) for index in range(12)]

        payload = builder.build(_context(widgets))

        self.assertLessEqual(estimateTokens(payload), 1200)

    def test_cards_survive_degradation_before_tables(self):
        builder = DashboardPayloadBuilder(fullRowLimit=40, compactRowLimit=5, tokenBudget=900)
        widgets = [_cardWidget("W0")] + [
            {**_barWidget(f"W{index}", rowCount=200), "chartType": "table",
             "data": [{"a": value, "b": value} for value in range(200)]}
            for index in range(1, 10)
        ]

        payload = builder.build(_context(widgets))

        self.assertIn("41234.5", payload)

    def test_degraded_widgets_are_replaced_by_summary_lines_not_dropped_silently(self):
        # Tight enough that compacting to 5 rows each is still over budget, so
        # the summarise rung of the ladder has to fire.
        builder = DashboardPayloadBuilder(fullRowLimit=40, compactRowLimit=5, tokenBudget=550)
        widgets = [_barWidget(f"W{index}", rowCount=300) for index in range(10)]

        payload = builder.build(_context(widgets))

        self.assertIn("summary:", payload)
        self.assertIn("### W0", payload)

    def test_small_dashboard_is_not_degraded(self):
        builder = DashboardPayloadBuilder(fullRowLimit=40, compactRowLimit=5, tokenBudget=12000)

        payload = builder.build(_context([_barWidget(rowCount=4)]))

        self.assertNotIn("summary:", payload)
        self.assertIn("region3,3", payload)


class EstimateTokensTests(unittest.TestCase):
    def test_estimate_scales_with_length(self):
        self.assertLess(estimateTokens("abcd"), estimateTokens("abcd" * 100))

    def test_empty_text_costs_nothing(self):
        self.assertEqual(0, estimateTokens(""))


if __name__ == "__main__":
    unittest.main()
