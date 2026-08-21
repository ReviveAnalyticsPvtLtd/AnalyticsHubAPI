import unittest

from nubrix.components.widgetProvenance import (
    extractProvenance,
    formatFilters,
    formatProvenance,
    referencedTables,
)

SINGLE_TABLE_CODE = '''
def panelChart(projectId, chartType, xAxis, yAxis, dataSourceName, aggregationMetric, tablesUsed, **kwargs):
    return None
panelChart(
    projectId = "p1",
    chartType = "bar",
    xAxis = "region",
    yAxis = "revenue",
    aggregationMetric = "sum",
    dataSourceName = "Sales 2024",
    tablesUsed = "sales_2024",
    index = None,
    columns = None,
    values = None,
    selectedColumns = None,
    mapType = "None",
    isFilterApplied = False,
    filters = None,
    geoCodeColumn = None
)
'''

FENCED_BLEND_CODE = '''```python
def panelChart(projectId, chartType, xAxis, yAxis, aggregationMetric, dataSourceName, tablesUsed, joinTypes=None, blendOn=None, **kwargs):
    return None
panelChart(
    projectId = "p1",
    chartType = "line",
    xAxis = "order_month",
    yAxis = "revenue",
    aggregationMetric = "mean",
    dataSourceName = "Blend",
    tablesUsed = ["orders", "customers"],
    joinTypes = ["inner"],
    blendOn = ["customer_id"],
    filters = [{"orders.region": ["East", "West"]}, {"orders.amount": {"min": 100}}]
)
```'''


class ExtractProvenanceTests(unittest.TestCase):
    def test_extracts_keyword_arguments_from_single_table_widget(self):
        provenance = extractProvenance(SINGLE_TABLE_CODE)

        self.assertEqual("bar", provenance["chartType"])
        self.assertEqual("region", provenance["xAxis"])
        self.assertEqual("revenue", provenance["yAxis"])
        self.assertEqual("sum", provenance["aggregationMetric"])
        self.assertEqual("sales_2024", provenance["tablesUsed"])

    def test_drops_placeholder_and_none_valued_keywords(self):
        provenance = extractProvenance(SINGLE_TABLE_CODE)

        self.assertNotIn("index", provenance)
        self.assertNotIn("geoCodeColumn", provenance)
        self.assertNotIn("mapType", provenance)

    def test_handles_code_fences_and_blended_tables(self):
        provenance = extractProvenance(FENCED_BLEND_CODE)

        self.assertEqual(["orders", "customers"], provenance["tablesUsed"])
        self.assertEqual(["inner"], provenance["joinTypes"])

    def test_returns_empty_dict_for_unparseable_or_missing_code(self):
        self.assertEqual({}, extractProvenance(None))
        self.assertEqual({}, extractProvenance(""))
        self.assertEqual({}, extractProvenance("def broken( :"))
        self.assertEqual({}, extractProvenance("x = 1"))


class FormattingTests(unittest.TestCase):
    def test_formats_aggregation_dimension_and_source(self):
        provenance = extractProvenance(SINGLE_TABLE_CODE)

        self.assertEqual("sum(revenue) by region from sales_2024", formatProvenance(provenance))

    def test_formats_list_and_range_filters(self):
        provenance = extractProvenance(FENCED_BLEND_CODE)

        self.assertEqual("region in [East, West]; amount min 100", formatFilters(provenance))

    def test_returns_none_when_nothing_to_format(self):
        self.assertIsNone(formatProvenance({}))
        self.assertIsNone(formatFilters({}))
        self.assertIsNone(formatFilters({"filters": None}))

    def test_referenced_tables_normalises_string_and_list_forms(self):
        self.assertEqual(["sales_2024"], referencedTables(extractProvenance(SINGLE_TABLE_CODE)))
        self.assertEqual(["orders", "customers"], referencedTables(extractProvenance(FENCED_BLEND_CODE)))
        self.assertEqual([], referencedTables({}))


if __name__ == "__main__":
    unittest.main()
