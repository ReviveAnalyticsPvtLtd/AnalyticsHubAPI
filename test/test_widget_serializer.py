import unittest

from nubrix.components.widgetSerializer import (
    downsampleRows,
    looksTemporal,
    normalizeWidgetData,
    renderTable,
    summariseRows,
)


class NormalizeWidgetDataTests(unittest.TestCase):
    def test_chartjs_series_becomes_columns_and_rows(self):
        widget = {
            "chartType": "bar",
            "xLabels": "region",
            "data": {
                "labels": ["East", "West"],
                "datasets": [{"label": "sum of revenue", "data": [100, 200]}],
            },
        }

        normalized = normalizeWidgetData(widget)

        self.assertEqual("series", normalized["kind"])
        self.assertEqual(["region", "sum of revenue"], normalized["columns"])
        self.assertEqual([["East", 100], ["West", 200]], normalized["rows"])
        self.assertEqual(2, normalized["rowCount"])

    def test_multiple_datasets_produce_one_column_each_with_unique_names(self):
        widget = {
            "chartType": "line",
            "xLabels": "month",
            "data": {
                "labels": ["Jan", "Feb"],
                "datasets": [
                    {"label": "revenue", "data": [1, 2]},
                    {"label": "revenue", "data": [3, 4]},
                ],
            },
        }

        normalized = normalizeWidgetData(widget)

        self.assertEqual(["month", "revenue", "revenue_2"], normalized["columns"])
        self.assertEqual([["Jan", 1, 3], ["Feb", 2, 4]], normalized["rows"])

    def test_ragged_datasets_are_padded_with_none(self):
        widget = {
            "chartType": "line",
            "data": {"labels": ["Jan", "Feb", "Mar"], "datasets": [{"label": "v", "data": [1, 2]}]},
        }

        normalized = normalizeWidgetData(widget)

        self.assertEqual([["Jan", 1], ["Feb", 2], ["Mar", None]], normalized["rows"])

    def test_card_widget_becomes_scalar(self):
        widget = {"chartType": "card", "label": "sum of revenue", "data": 41234.5}

        normalized = normalizeWidgetData(widget)

        self.assertEqual("scalar", normalized["kind"])
        self.assertEqual(41234.5, normalized["value"])
        self.assertEqual("sum of revenue", normalized["label"])

    def test_card_widget_with_null_data_still_normalises(self):
        normalized = normalizeWidgetData({"chartType": "card", "label": "x", "data": None})

        self.assertEqual("scalar", normalized["kind"])
        self.assertIsNone(normalized["value"])

    def test_table_records_become_union_of_keys(self):
        widget = {"chartType": "table", "data": [{"a": 1, "b": 2}, {"a": 3, "c": 4}]}

        normalized = normalizeWidgetData(widget)

        self.assertEqual("records", normalized["kind"])
        self.assertEqual(["a", "b", "c"], normalized["columns"])
        self.assertEqual([[1, 2, None], [3, None, 4]], normalized["rows"])

    def test_pivot_matrix_becomes_index_plus_columns(self):
        widget = {"chartType": "pivot", "data": {"revenue": {"East": 10, "West": 20}}}

        normalized = normalizeWidgetData(widget)

        self.assertEqual("matrix", normalized["kind"])
        self.assertEqual(["index", "revenue"], normalized["columns"])
        self.assertEqual([["East", 10], ["West", 20]], normalized["rows"])

    def test_geomap_points_become_records(self):
        widget = {"chartType": "geoMap", "data": {"points": [{"lat": 1.0, "lon": 2.0, "value": 7}]}}

        normalized = normalizeWidgetData(widget)

        self.assertEqual("points", normalized["kind"])
        self.assertEqual([[1.0, 2.0, 7]], normalized["rows"])

    def test_unrecognised_shape_is_reported_as_unknown(self):
        normalized = normalizeWidgetData({"chartType": "bar", "data": {"nope": True}})

        self.assertEqual("unknown", normalized["kind"])
        self.assertEqual(0, normalized["rowCount"])


class LooksTemporalTests(unittest.TestCase):
    def test_iso_dates_are_temporal(self):
        self.assertTrue(looksTemporal(["2024-01-01", "2024-02-01", "2024-03-01"]))

    def test_year_month_is_temporal(self):
        self.assertTrue(looksTemporal(["2024-01", "2024-02"]))

    def test_month_names_are_temporal(self):
        self.assertTrue(looksTemporal(["Jan", "Feb", "Mar"]))

    def test_categories_are_not_temporal(self):
        self.assertFalse(looksTemporal(["East", "West", "North"]))

    def test_empty_input_is_not_temporal(self):
        self.assertFalse(looksTemporal([]))


class DownsampleRowsTests(unittest.TestCase):
    def test_rows_within_limit_are_returned_untouched(self):
        rows = [["a", 1], ["b", 2]]

        result, note = downsampleRows(["k", "v"], rows, limit=10)

        self.assertEqual(rows, result)
        self.assertIsNone(note)

    def test_temporal_series_keeps_first_last_and_extremes_in_order(self):
        rows = [[f"2024-{month:02d}-01", month] for month in range(1, 13)]
        rows[5][1] = 999

        result, note = downsampleRows(["month", "value"], rows, limit=6)

        self.assertEqual(6, len(result))
        self.assertEqual(rows[0], result[0])
        self.assertEqual(rows[-1], result[-1])
        self.assertIn(["2024-06-01", 999], result)
        self.assertEqual(result, sorted(result, key=lambda row: row[0]))
        self.assertIn("12", note)

    def test_categorical_series_keeps_top_values_and_buckets_the_tail(self):
        rows = [[f"cat{index}", index] for index in range(20)]

        result, note = downsampleRows(["category", "value"], rows, limit=5)

        self.assertEqual(5, len(result))
        self.assertEqual(["cat19", 19], result[0])
        self.assertEqual("(other 16 categories)", result[-1][0])
        self.assertEqual(sum(range(16)), result[-1][1])
        self.assertIn("top", note)

    def test_ranking_column_at_index_zero_reports_remainder_in_the_note(self):
        # The label column and the ranking column are the same, so a bucket row
        # would have to overwrite one with the other.
        rows = [[index, f"name{index}"] for index in range(20)]

        result, note = downsampleRows(["order_id", "label"], rows, limit=5)

        self.assertEqual(5, len(result))
        self.assertEqual([19, "name19"], result[0])
        self.assertNotIn(None, [row[0] for row in result])
        self.assertIn("remaining 15 rows total 105", note)

    def test_rows_without_a_numeric_column_are_head_truncated(self):
        rows = [["a", "x"], ["b", "y"], ["c", "z"]]

        result, note = downsampleRows(["k", "v"], rows, limit=2)

        self.assertEqual([["a", "x"], ["b", "y"]], result)
        self.assertIn("3", note)


class RenderTableTests(unittest.TestCase):
    def test_renders_header_and_rows_as_csv(self):
        text = renderTable(["region", "revenue"], [["East", 100], ["West", 200]])

        self.assertEqual("region,revenue\nEast,100\nWest,200", text)

    def test_rounds_floats_and_blanks_nulls(self):
        text = renderTable(["k", "v"], [["a", 1.23456789], ["b", None]])

        self.assertEqual("k,v\na,1.2346\nb,", text)

    def test_quotes_values_containing_separators(self):
        text = renderTable(["k", "v"], [["a,b", "line1\nline2"]])

        self.assertEqual('k,v\n"a,b","line1 line2"', text)


class SummariseRowsTests(unittest.TestCase):
    def test_summarises_first_last_extremes_and_change(self):
        rows = [["Jan", 100], ["Feb", 150], ["Mar", 200]]

        summary = summariseRows(["month", "revenue"], rows)

        self.assertIn("revenue", summary)
        self.assertIn("first=100", summary)
        self.assertIn("last=200", summary)
        self.assertIn("min=100", summary)
        self.assertIn("max=200", summary)
        self.assertIn("change=+100.0%", summary)

    def test_returns_none_without_numeric_columns(self):
        self.assertIsNone(summariseRows(["a", "b"], [["x", "y"]]))


if __name__ == "__main__":
    unittest.main()
