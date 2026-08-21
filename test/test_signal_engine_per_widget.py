import sys
import types
import unittest


def _installLoggerStub():
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


_installLoggerStub()

from nubrix.components.signalEngine import SignalEngine, formatWidgetSignals  # noqa: E402


def _timeSeriesWidget(ref="W1"):
    return {
        "ref": ref,
        "title": "Revenue by Month",
        "chartType": "line",
        "xLabels": "month",
        "data": {
            "labels": [f"2024-{month:02d}-01" for month in range(1, 13)],
            "datasets": [{"label": "revenue", "data": [100] * 6 + [150] * 6}],
        },
    }


def _categoricalWidget(ref="W2"):
    return {
        "ref": ref,
        "title": "Revenue by Region",
        "chartType": "bar",
        "xLabels": "region",
        "data": {
            "labels": ["East", "West", "North", "South"],
            "datasets": [{"label": "revenue", "data": [700, 200, 60, 40]}],
        },
    }


class BuildWidgetSignalsTests(unittest.TestCase):
    def setUp(self):
        self.engine = SignalEngine()

    def test_time_series_widget_produces_period_deltas(self):
        signals = self.engine.buildWidgetSignals(_timeSeriesWidget())

        deltas = signals["periodDeltas"]
        self.assertEqual(1, len(deltas))
        self.assertEqual("revenue", deltas[0]["column"])
        self.assertEqual(50.0, deltas[0]["pctChange"])

    def test_categorical_widget_produces_concentration_and_contributors(self):
        signals = self.engine.buildWidgetSignals(_categoricalWidget())

        self.assertEqual("revenue", signals["concentration"]["valueCol"])
        self.assertEqual(0.96, signals["concentration"]["top3Share"])
        self.assertEqual("East", signals["topContributors"][0]["group"])

    def test_card_widget_produces_no_signals(self):
        signals = self.engine.buildWidgetSignals({"ref": "W3", "chartType": "card", "data": 10})

        self.assertEqual({}, signals)

    def test_widget_with_single_numeric_column_reports_correlations_skipped(self):
        signals = self.engine.buildWidgetSignals(_timeSeriesWidget())

        self.assertTrue(signals["correlations"]["skipped"])


class BuildStatisticalSummaryTests(unittest.TestCase):
    def setUp(self):
        self.engine = SignalEngine()

    def test_summary_is_keyed_by_widget_reference(self):
        summary = self.engine.buildStatisticalSummary([_timeSeriesWidget("W1"), _categoricalWidget("W2")])

        self.assertEqual(2, summary["widgetCount"])
        self.assertEqual({"W1", "W2"}, set(summary["perWidget"]))

    def test_summary_falls_back_to_id_then_positional_reference(self):
        withId = {key: value for key, value in _categoricalWidget().items() if key != "ref"}
        withId["id"] = "widget-abc"
        withNeither = {key: value for key, value in _categoricalWidget().items() if key != "ref"}

        byId = self.engine.buildStatisticalSummary([withId])
        byPosition = self.engine.buildStatisticalSummary([withNeither])

        self.assertIn("widget-abc", byId["perWidget"])
        self.assertIn("W1", byPosition["perWidget"])

    def test_summary_has_no_cross_widget_correlations_key(self):
        summary = self.engine.buildStatisticalSummary([_timeSeriesWidget("W1"), _categoricalWidget("W2")])

        self.assertNotIn("correlations", summary)

    def test_cross_widget_merge_helper_is_removed(self):
        self.assertFalse(hasattr(SignalEngine, "_mergeAllWidgetData"))

    def test_empty_chart_data_returns_empty_summary(self):
        summary = self.engine.buildStatisticalSummary([])

        self.assertEqual({"widgetCount": 0, "perWidget": {}}, summary)


class FormatWidgetSignalsTests(unittest.TestCase):
    def test_formats_delta_and_concentration_into_one_line(self):
        engine = SignalEngine()
        line = formatWidgetSignals(engine.buildWidgetSignals(_categoricalWidget()))

        self.assertIn("top3Share=0.96", line)

    def test_returns_none_for_empty_signals(self):
        self.assertIsNone(formatWidgetSignals({}))


if __name__ == "__main__":
    unittest.main()
