"""
widgetSerializer.py

Normalizes the heterogeneous `data` payloads a dashboard widget can carry into a
single tabular form, downsamples that form under a row budget without discarding
the rows that carry signal, and renders it as compact CSV for LLM consumption.

Widget data arrives in five shapes: Chart.js series objects, scalar card values,
record lists (tables), nested column/index maps (pivots), and geo point lists.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "normalizeWidgetData",
    "looksTemporal",
    "downsampleRows",
    "renderTable",
    "summariseRows",
]

import re

FLOAT_PRECISION = 4
TEMPORAL_MATCH_RATIO = 0.6

_ISO_DATE_PATTERN = re.compile(r"^\d{4}[-/](0?[1-9]|1[0-2])([-/](0?[1-9]|[12]\d|3[01]))?")
_MONTH_NAMES = {
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
}
_QUARTER_PATTERN = re.compile(r"^(q[1-4][- ]?\d{2,4}|\d{4}[- ]?q[1-4])$")


def _uniqueColumnNames(names: list[str]) -> list[str]:
    """Disambiguates repeated column names by appending an occurrence index."""
    seen = {}
    unique = []
    for name in names:
        count = seen.get(name, 0) + 1
        seen[name] = count
        unique.append(name if count == 1 else f"{name}_{count}")
    return unique


def _dimensionName(widget: dict) -> str:
    """Derives the name of the category/dimension column from widget config."""
    xLabels = widget.get("xLabels")
    if isinstance(xLabels, str) and xLabels.strip():
        return xLabels.strip()
    return "category"


def _normalizeSeries(widget: dict, data: dict) -> dict:
    """Converts a Chart.js `{labels, datasets}` payload into columns and rows."""
    datasets = [dataset for dataset in data.get("datasets") or [] if isinstance(dataset, dict)]
    labels = data.get("labels")
    if not isinstance(labels, list):
        labels = []

    columnNames = [_dimensionName(widget)]
    seriesValues = []
    for index, dataset in enumerate(datasets):
        columnNames.append(str(dataset.get("label") or f"series_{index + 1}"))
        values = dataset.get("data")
        seriesValues.append(values if isinstance(values, list) else [])

    rowCount = max([len(labels)] + [len(values) for values in seriesValues] or [0])
    rows = []
    for rowIndex in range(rowCount):
        row = [labels[rowIndex] if rowIndex < len(labels) else None]
        for values in seriesValues:
            row.append(values[rowIndex] if rowIndex < len(values) else None)
        rows.append(row)

    return {
        "kind": "series",
        "columns": _uniqueColumnNames(columnNames),
        "rows": rows,
        "rowCount": len(rows),
    }


def _normalizeRecords(records: list, kind: str) -> dict:
    """Converts a list of dicts into columns (union of keys, first-seen order) and rows."""
    columns = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in record:
            if key not in columns:
                columns.append(str(key))
    rows = [
        [record.get(column) for column in columns]
        for record in records
        if isinstance(record, dict)
    ]
    return {"kind": kind, "columns": columns, "rows": rows, "rowCount": len(rows)}


def _normalizeMatrix(data: dict) -> dict | None:
    """Converts a pivot `{column: {index: value}}` map into an indexed table."""
    columns = [key for key, value in data.items() if isinstance(value, dict)]
    if not columns:
        return None

    indexKeys = []
    for column in columns:
        for indexKey in data[column]:
            if indexKey not in indexKeys:
                indexKeys.append(indexKey)

    rows = [[indexKey] + [data[column].get(indexKey) for column in columns] for indexKey in indexKeys]
    return {
        "kind": "matrix",
        "columns": _uniqueColumnNames(["index"] + [str(column) for column in columns]),
        "rows": rows,
        "rowCount": len(rows),
    }


def normalizeWidgetData(widget: dict) -> dict:
    """
    Normalizes a widget's data payload into a common tabular representation.

    Args:
        widget (dict): Widget config carrying `chartType`, `data`, `xLabels`, `label`.

    Returns:
        dict: For tabular kinds, `{kind, columns, rows, rowCount}`. For cards,
            `{kind: "scalar", label, value, rowCount}`. Unrecognised payloads
            return kind "unknown" with no rows.
    """
    chartType = str(widget.get("chartType") or "").strip()
    data = widget.get("data")

    if chartType == "card":
        return {"kind": "scalar", "label": widget.get("label"), "value": data, "rowCount": 1}

    if isinstance(data, dict):
        if isinstance(data.get("datasets"), list):
            return _normalizeSeries(widget, data)
        if isinstance(data.get("points"), list):
            return _normalizeRecords(data["points"], "points")
        matrix = _normalizeMatrix(data)
        if matrix is not None:
            return matrix
    elif isinstance(data, list):
        return _normalizeRecords(data, "records")
    elif data is not None:
        return {"kind": "scalar", "label": widget.get("label"), "value": data, "rowCount": 1}

    return {"kind": "unknown", "columns": [], "rows": [], "rowCount": 0}


def looksTemporal(values: list) -> bool:
    """
    Reports whether a label sequence reads as a time axis.

    Uses ISO dates, year-month strings, quarter codes, and month-name prefixes.
    A majority of non-null values must match for the sequence to count.
    """
    candidates = [value for value in values if value is not None]
    if not candidates:
        return False

    matches = 0
    for value in candidates:
        text = str(value).strip().lower()
        if _ISO_DATE_PATTERN.match(text) or _QUARTER_PATTERN.match(text) or text[:3] in _MONTH_NAMES:
            matches += 1
    return matches >= max(1, int(len(candidates) * TEMPORAL_MATCH_RATIO))


def _firstNumericColumnIndex(columns: list[str], rows: list[list]) -> int | None:
    """Returns the index of the first column whose values are predominantly numeric."""
    for columnIndex in range(len(columns)):
        values = [row[columnIndex] for row in rows if columnIndex < len(row)]
        numeric = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if numeric and len(numeric) >= max(1, int(len(values) * 0.6)):
            return columnIndex
    return None


def _evenlySpacedIndices(total: int, count: int) -> list[int]:
    """Returns `count` indices spread evenly across `range(total)`."""
    if count >= total:
        return list(range(total))
    step = (total - 1) / float(count - 1) if count > 1 else 0.0
    return sorted({int(round(position * step)) for position in range(count)})


def downsampleRows(columns: list[str], rows: list[list], limit: int) -> tuple[list[list], str | None]:
    """
    Reduces rows to at most `limit` while preserving the rows that carry signal.

    Temporal series keep the first, last, minimum, and maximum rows plus evenly
    spaced samples, in original order. Categorical series keep the highest-valued
    rows and aggregate the remainder into a single bucket row. Sequences without
    a numeric column are truncated from the head.

    Returns:
        tuple[list[list], str | None]: The reduced rows and a note describing
            the reduction, or None when no reduction happened.
    """
    if limit <= 0 or len(rows) <= limit:
        return rows, None

    originalCount = len(rows)
    valueIndex = _firstNumericColumnIndex(columns, rows)
    if valueIndex is None:
        return rows[:limit], f"showing first {limit} of {originalCount} rows"

    labels = [row[0] if row else None for row in rows]
    if looksTemporal(labels):
        keep = {0, originalCount - 1}
        numericPairs = [
            (index, row[valueIndex])
            for index, row in enumerate(rows)
            if isinstance(row[valueIndex], (int, float)) and not isinstance(row[valueIndex], bool)
        ]
        if numericPairs:
            keep.add(max(numericPairs, key=lambda pair: pair[1])[0])
            keep.add(min(numericPairs, key=lambda pair: pair[1])[0])
        remaining = limit - len(keep)
        if remaining > 0:
            keep.update(_evenlySpacedIndices(originalCount, remaining + 2))

        selected = sorted(keep)
        if len(selected) > limit:
            # Trim from the middle so the first and last rows always survive.
            selected = [selected[0]] + selected[1:-1][: limit - 2] + [selected[-1]]
        note = f"downsampled to {len(selected)} of {originalCount} rows (endpoints, extremes, even sample)"
        return [rows[index] for index in selected], note

    ranked = sorted(
        rows,
        key=lambda row: row[valueIndex] if isinstance(row[valueIndex], (int, float)) and not isinstance(row[valueIndex], bool) else float("-inf"),
        reverse=True,
    )
    # A bucket row is only possible when column 0 is free to hold its label,
    # so only then does one of the `limit` slots need reserving for it.
    bucketable = valueIndex != 0
    headSize = limit - 1 if bucketable else limit
    head = ranked[:headSize]
    tail = ranked[headSize:]
    tailTotal = sum(
        row[valueIndex]
        for row in tail
        if isinstance(row[valueIndex], (int, float)) and not isinstance(row[valueIndex], bool)
    )
    tailTotal = round(tailTotal, FLOAT_PRECISION)
    if not bucketable:
        # The ranking column is also the label column, so there is nowhere to put
        # a bucket label without overwriting the aggregate. State it in the note
        # instead of emitting a row whose label column holds a number.
        note = (
            f"top {len(head)} of {originalCount} rows by {columns[valueIndex]}; "
            f"remaining {len(tail)} rows total {tailTotal}"
        )
        return head, note

    bucket = [None] * len(columns)
    bucket[0] = f"(other {len(tail)} categories)"
    bucket[valueIndex] = tailTotal
    note = f"top {len(head)} of {originalCount} rows by {columns[valueIndex]}, remainder aggregated"
    return head + [bucket], note


def _renderCell(value: object) -> str:
    """Formats a single cell: rounds floats, blanks nulls, escapes separators."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{round(value, FLOAT_PRECISION)}"
    else:
        text = str(value)
    needsQuoting = any(character in text for character in (",", '"', "\n", "\r"))
    text = text.replace("\r", " ").replace("\n", " ")
    if needsQuoting:
        text = '"' + text.replace('"', '""') + '"'
    return text


def renderTable(columns: list[str], rows: list[list]) -> str:
    """Renders columns and rows as compact CSV text with no trailing newline."""
    lines = [",".join(_renderCell(column) for column in columns)]
    lines.extend(",".join(_renderCell(cell) for cell in row) for row in rows)
    return "\n".join(lines)


def summariseRows(columns: list[str], rows: list[list]) -> str | None:
    """
    Produces a single aggregate line per numeric column for degraded rendering.

    Returns:
        str | None: e.g. "revenue: first=100 last=200 min=100 max=200
            mean=150.0 change=+100.0%", or None when nothing is numeric.
    """
    if not rows:
        return None

    summaries = []
    for columnIndex, column in enumerate(columns):
        values = [
            row[columnIndex]
            for row in rows
            if columnIndex < len(row)
            and isinstance(row[columnIndex], (int, float))
            and not isinstance(row[columnIndex], bool)
        ]
        if not values:
            continue
        first, last = values[0], values[-1]
        change = None if first == 0 else round(((last - first) / abs(first)) * 100, 1)
        parts = [
            f"first={round(first, FLOAT_PRECISION)}",
            f"last={round(last, FLOAT_PRECISION)}",
            f"min={round(min(values), FLOAT_PRECISION)}",
            f"max={round(max(values), FLOAT_PRECISION)}",
            f"mean={round(sum(values) / len(values), FLOAT_PRECISION)}",
        ]
        if change is not None:
            parts.append(f"change={change:+}%")
        summaries.append(f"{column}: " + " ".join(parts))
    return "; ".join(summaries) if summaries else None
