"""
widgetProvenance.py

Recovers the analytical semantics of a dashboard widget from the Python source
stored in its `generatedCode` field. Widget code always ends in a top-level
`panelChart(...)` call whose keyword arguments name the measure, the dimension,
the aggregation, the source tables, and any applied filters. Extracting those
lets the insight pipeline describe what each number actually means.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["extractProvenance", "formatProvenance", "formatFilters", "referencedTables"]

import ast

PANEL_CHART_FUNCTION = "panelChart"

CAPTURED_KEYWORDS = (
    "chartType",
    "xAxis",
    "yAxis",
    "aggregationMetric",
    "dataSourceName",
    "tablesUsed",
    "joinTypes",
    "blendOn",
    "index",
    "columns",
    "values",
    "selectedColumns",
    "geoCodeColumn",
    "filters",
)

# Widget code substitutes unset template placeholders with these literals rather
# than omitting the keyword, so they must be treated as absent.
NULL_LITERALS = {"", "none", "null", "nan", "undefined"}

MAX_FILTER_VALUES_SHOWN = 5


def _stripCodeFences(code: str) -> str:
    """Removes markdown code fences so the body can be parsed as Python."""
    if "```" not in code:
        return code
    return "\n".join(line for line in code.splitlines() if not line.strip().startswith("```"))


def _isNullLiteral(value: object) -> bool:
    """Reports whether a parsed value is a placeholder standing in for absence."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in NULL_LITERALS:
        return True
    return False


def extractProvenance(generatedCode: str | None) -> dict:
    """
    Extracts the keyword arguments of the top-level `panelChart` call.

    Args:
        generatedCode (str | None): Widget source, optionally fenced.

    Returns:
        dict: Captured keyword arguments with placeholder values removed.
            Empty dict when the code is missing, unparseable, or has no call.
    """
    if not isinstance(generatedCode, str) or not generatedCode.strip():
        return {}
    try:
        tree = ast.parse(_stripCodeFences(generatedCode))
    except (SyntaxError, ValueError):
        return {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != PANEL_CHART_FUNCTION:
            continue

        provenance = {}
        for keyword in node.keywords:
            if keyword.arg not in CAPTURED_KEYWORDS:
                continue
            try:
                value = ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError):
                continue
            if _isNullLiteral(value):
                continue
            provenance[keyword.arg] = value
        if provenance:
            return provenance
    return {}


def referencedTables(provenance: dict) -> list[str]:
    """Returns the source tables a widget reads from, normalised to a list."""
    tables = provenance.get("tablesUsed")
    if isinstance(tables, str):
        tables = [tables]
    if not isinstance(tables, (list, tuple)):
        return []
    return [str(table) for table in tables if not _isNullLiteral(table)]


def formatProvenance(provenance: dict) -> str | None:
    """
    Renders the measure, dimension, and source as a single line.

    Returns:
        str | None: e.g. "sum(revenue) by region from sales_2024", or None
            when the provenance carries nothing describable.
    """
    if not provenance:
        return None

    parts = []
    measure = provenance.get("yAxis")
    aggregation = provenance.get("aggregationMetric")
    if measure:
        parts.append(f"{aggregation}({measure})" if aggregation else str(measure))

    dimension = provenance.get("xAxis")
    if dimension:
        parts.append(f"by {dimension}")

    tables = referencedTables(provenance)
    if tables:
        parts.append("from " + ", ".join(tables))

    return " ".join(parts) if parts else None


def formatFilters(provenance: dict) -> str | None:
    """
    Renders the filters applied to a widget as a compact clause list.

    Returns:
        str | None: e.g. "region in [East, West]; amount min 100", or None
            when no filters are recorded.
    """
    filters = provenance.get("filters")
    if not isinstance(filters, (list, tuple)) or not filters:
        return None

    clauses = []
    for item in filters:
        if not isinstance(item, dict):
            continue
        for columnPath, condition in item.items():
            column = str(columnPath).split(".")[-1]
            if isinstance(condition, dict):
                for operator, operand in condition.items():
                    clauses.append(f"{column} {operator} {operand}")
            elif isinstance(condition, (list, tuple, set)):
                values = list(condition)
                shown = ", ".join(str(value) for value in values[:MAX_FILTER_VALUES_SHOWN])
                if len(values) > MAX_FILTER_VALUES_SHOWN:
                    shown += f", +{len(values) - MAX_FILTER_VALUES_SHOWN} more"
                clauses.append(f"{column} in [{shown}]")
            else:
                clauses.append(f"{column} = {condition}")
    return "; ".join(clauses) if clauses else None
