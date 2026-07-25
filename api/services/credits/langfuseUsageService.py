"""
langfuseUsageService.py

Queries the Langfuse Metrics API (v4 SDK, ``client.api.metrics.metrics``)
for per-operation and per-model usage breakdowns within a user's current
billing period.

Falls back gracefully when Langfuse is not configured or the
metrics endpoint returns an unexpected shape.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["getUsageBreakdown"]

from utils.langfuseClient import langfuseClient
from utils.logger import logger
import json


def getUsageBreakdown(
    userId: str,
    fromTimestamp: str,
    toTimestamp: str,
) -> dict | None:
    """
    Query Langfuse for token-level usage grouped by operation name
    and by model name.

    Args:
        userId:        Platform user ID (matches the user_id set on traces).
        fromTimestamp:  ISO-8601 start of the billing period.
        toTimestamp:    ISO-8601 end of the billing period.

    Returns:
        dict with ``byOperation``, ``byModel``, and ``langfuseAvailable``
        keys, or ``None`` when Langfuse is unavailable.
    """
    if langfuseClient is None:
        return None

    try:
        byOperation = _queryByDimension(
            userId, fromTimestamp, toTimestamp, dimension="traceName"
        )
        byModel = _queryByDimension(
            userId, fromTimestamp, toTimestamp, dimension="providedModelName"
        )

        return {
            "byOperation": byOperation,
            "byModel": byModel,
            "langfuseAvailable": True,
        }
    except Exception as e:
        logger.warning(f"Langfuse usage breakdown failed for userId={userId}: {e}")
        return None


def _extractMetric(row: dict, measure: str, aggregation: str) -> int:
    """
    Tolerantly extract a metric value from a Langfuse metrics response row.

    The v4 API may return the value under the raw measure name
    (e.g. ``totalTokens``) or under an alias like ``sum_totalTokens``
    or ``count_count``.
    """
    for key in (measure, f"{aggregation}_{measure}"):
        val = row.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0
    return 0


def _queryByDimension(
    userId: str,
    fromTimestamp: str,
    toTimestamp: str,
    dimension: str,
) -> list[dict]:
    """
    Execute a single Langfuse Metrics v4 API query grouped by *dimension*.

    ``dimension`` is ``traceName`` (for by-operation) or
    ``providedModelName`` (for by-model).  ``userId`` is a high-cardinality
    field and must be passed as a filter, not a dimension.
    """
    query = json.dumps({
        "view": "observations",
        "dimensions": [{"field": dimension}],
        "metrics": [
            {"measure": "totalTokens", "aggregation": "sum"},
            {"measure": "count", "aggregation": "count"},
        ],
        "filters": [
            {
                "column": "userId",
                "operator": "=",
                "value": userId,
                "type": "string",
            },
        ],
        "fromTimestamp": fromTimestamp,
        "toTimestamp": toTimestamp,
    })

    response = langfuseClient.api.metrics.metrics(query=query)
    data = getattr(response, "data", None) or []

    rows: list[dict] = []
    for entry in data:
        entryDict = entry if isinstance(entry, dict) else vars(entry) if hasattr(entry, "__dict__") else {}

        tokens = _extractMetric(entryDict, "totalTokens", "sum")
        calls = _extractMetric(entryDict, "count", "count")

        if dimension == "traceName":
            label = entryDict.get("traceName") or entryDict.get("dimension") or "unknown"
            rows.append({"tag": str(label), "totalTokens": tokens, "callCount": calls})
        else:
            model = entryDict.get("providedModelName") or entryDict.get("dimension") or "unknown"
            rows.append({"model": str(model), "totalTokens": tokens, "callCount": calls})

    return rows
