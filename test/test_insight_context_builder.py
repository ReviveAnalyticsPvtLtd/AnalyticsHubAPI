import sys
import types
import unittest


def _importContextBuilder():
    """
    Imports InsightContextBuilder behind the minimum stubs it needs.

    `utils.logger` is stubbed for the session because its logtail dependency is
    not installed. `api.commons` needs live Supabase credentials, so it is
    stubbed only for the duration of this import and then restored -- leaving it
    in place would hide names (verifyUser, verifyToken) that later test modules
    import from the real module. `utils.exceptionHandler` imports cleanly and is
    deliberately left alone for the same reason.
    """
    loggerModule = types.ModuleType("utils.logger")
    loggerModule.logger = types.SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
    )
    sys.modules["utils.logger"] = loggerModule

    savedCommons = sys.modules.get("api.commons")
    commonsModule = types.ModuleType("api.commons")
    commonsModule.client = object()
    sys.modules["api.commons"] = commonsModule
    try:
        from nubrix.components.insightContextBuilder import InsightContextBuilder

        return InsightContextBuilder
    finally:
        if savedCommons is None:
            sys.modules.pop("api.commons", None)
        else:
            sys.modules["api.commons"] = savedCommons


InsightContextBuilder = _importContextBuilder()

DASHBOARD_CONFIG = {
    "page_1": {
        "name": "Overview",
        "widgets": [
            {
                "id": "widget-a",
                "title": "Revenue by Region",
                "chartType": "bar",
                "data": {"labels": ["East"], "datasets": [{"label": "revenue", "data": [1]}]},
                "xLabels": "region",
                "yLabels": "revenue",
                "label": None,
                "map": None,
                "generatedCode": "panelChart(xAxis = 'region')",
            }
        ],
    },
    "page_2": {
        "name": "Detail",
        "widgets": [{"id": "widget-b", "title": "Tickets", "chartType": "table", "data": []}],
    },
}


class ExtractChartDataTests(unittest.TestCase):
    def setUp(self):
        self.builder = object.__new__(InsightContextBuilder)

    def test_widget_carries_generated_code_and_id(self):
        chartData = self.builder._extractChartData(DASHBOARD_CONFIG, "page_1")

        self.assertEqual("widget-a", chartData[0]["id"])
        self.assertEqual("panelChart(xAxis = 'region')", chartData[0]["generatedCode"])

    def test_widgets_receive_sequential_references(self):
        chartData = self.builder._extractChartData(DASHBOARD_CONFIG, "page_1")

        self.assertEqual(["W1"], [widget["ref"] for widget in chartData])

    def test_widget_records_its_page(self):
        chartData = self.builder._extractChartData(DASHBOARD_CONFIG, "page_1")

        self.assertEqual("page_1", chartData[0]["pageId"])

    def test_page_filter_limits_widgets_to_that_page(self):
        chartData = self.builder._extractChartData(DASHBOARD_CONFIG, "page_2")

        self.assertEqual(1, len(chartData))
        self.assertEqual("widget-b", chartData[0]["id"])

    def test_unknown_page_yields_no_widgets(self):
        self.assertEqual([], self.builder._extractChartData(DASHBOARD_CONFIG, "missing"))

    def test_empty_config_yields_no_widgets(self):
        self.assertEqual([], self.builder._extractChartData({}, "page_1"))


if __name__ == "__main__":
    unittest.main()
