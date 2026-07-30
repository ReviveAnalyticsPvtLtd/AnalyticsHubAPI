"""
signalEngine.py

Deterministic statistical signal extraction for the dashboard insight pipeline.
Computes period deltas, anomaly detection, concentration indices, top contributors,
and pairwise correlations for each widget individually, producing signals the
payload builder inlines beneath the widget they describe.

Signals are always scoped to a single widget. Rows of different widgets share no
index, so no statistic is ever computed across widget boundaries.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["SignalEngine", "formatWidgetSignals"]


from nubrix.components.widgetSerializer import normalizeWidgetData
from utils.exceptionHandler import CustomException
from utils.logger import logger
import pandas as pd
import numpy as np


class SignalEngine:
    """
    Provides deterministic statistical computations over widget DataFrames
    to produce evidence signals that augment LLM-based insight generation.
    """
    def __init__(self):
        """Initializes the SignalEngine."""
        logger.info("Initializing SignalEngine.")

    @staticmethod
    def computePeriodDeltas(df: pd.DataFrame, dateCol: str, valueCols: list[str]) -> list[dict]:
        """
        Computes period-over-period percentage change for the given value columns.

        Args:
            df (pd.DataFrame): Source data.
            dateCol (str): Name of the date/time column.
            valueCols (list[str]): Numeric columns to compute deltas for.

        Returns:
            list[dict]: Per-column delta summaries with latestValue, previousValue, and pctChange.
        """
        if df.empty or dateCol not in df.columns:
            return []
        try:
            working = df.copy()
            working[dateCol] = pd.to_datetime(working[dateCol], errors="coerce")
            working = working.dropna(subset=[dateCol]).sort_values(dateCol)
            if len(working) < 2:
                return []

            midpoint = len(working) // 2
            earlier = working.iloc[:midpoint]
            later = working.iloc[midpoint:]

            deltas = []
            for col in valueCols:
                if col not in working.columns or not pd.api.types.is_numeric_dtype(working[col]):
                    continue
                prevMean = earlier[col].mean()
                currMean = later[col].mean()
                if prevMean == 0:
                    pctChange = None
                else:
                    pctChange = round(((currMean - prevMean) / abs(prevMean)) * 100, 2)
                deltas.append({
                    "column": col,
                    "previousPeriodMean": round(float(prevMean), 4) if pd.notna(prevMean) else None,
                    "currentPeriodMean": round(float(currMean), 4) if pd.notna(currMean) else None,
                    "pctChange": pctChange,
                })
            return deltas
        except Exception:
            return []

    @staticmethod
    def detectAnomalies(df: pd.DataFrame, valueCols: list[str], method: str = "mad") -> list[dict]:
        """
        Detects anomalous rows using Median Absolute Deviation (MAD).

        Args:
            df (pd.DataFrame): Source data.
            valueCols (list[str]): Numeric columns to check for anomalies.
            method (str): Detection method. Currently supports "mad".

        Returns:
            list[dict]: Per-column anomaly summaries with count, threshold, and sample indices.
        """
        if df.empty:
            return []
        anomalies = []
        try:
            for col in valueCols:
                if col not in df.columns or not pd.api.types.is_numeric_dtype(df[col]):
                    continue
                series = df[col].dropna()
                if len(series) < 5:
                    continue
                median = series.median()
                mad = np.median(np.abs(series - median))
                if mad == 0:
                    continue
                threshold = 3.5
                modifiedZScores = 0.6745 * (series - median) / mad
                outlierMask = np.abs(modifiedZScores) > threshold
                outlierCount = int(outlierMask.sum())
                if outlierCount > 0:
                    anomalies.append({
                        "column": col,
                        "method": method,
                        "outlierCount": outlierCount,
                        "totalRows": len(series),
                        "median": round(float(median), 4),
                        "mad": round(float(mad), 4),
                    })
            return anomalies
        except Exception:
            return []

    @staticmethod
    def computeConcentration(df: pd.DataFrame, groupCol: str, valueCol: str) -> dict:
        """
        Computes concentration metrics (Herfindahl index, top-3 share) for a categorical grouping.

        Args:
            df (pd.DataFrame): Source data.
            groupCol (str): Categorical column to group by.
            valueCol (str): Numeric column to aggregate.

        Returns:
            dict: Concentration metrics or empty dict if computation is not possible.
        """
        if df.empty or groupCol not in df.columns or valueCol not in df.columns:
            return {}
        try:
            grouped = df.groupby(groupCol)[valueCol].sum()
            total = grouped.sum()
            if total == 0:
                return {}
            shares = grouped / total
            hhi = round(float((shares ** 2).sum()), 4)
            top3 = shares.nlargest(3)
            top3Share = round(float(top3.sum()), 4)
            return {
                "groupCol": groupCol,
                "valueCol": valueCol,
                "herfindahlIndex": hhi,
                "top3Share": top3Share,
                "top3Groups": top3.index.tolist(),
                "numGroups": len(grouped),
            }
        except Exception:
            return {}

    @staticmethod
    def computeTopContributors(df: pd.DataFrame, groupCol: str, valueCol: str, topN: int = 5) -> list[dict]:
        """
        Identifies the top-N contributors to a metric by group.

        Args:
            df (pd.DataFrame): Source data.
            groupCol (str): Categorical column to group by.
            valueCol (str): Numeric column to aggregate.
            topN (int): Number of top contributors to return.

        Returns:
            list[dict]: Top contributors with group name, value, and share.
        """
        if df.empty or groupCol not in df.columns or valueCol not in df.columns:
            return []
        try:
            grouped = df.groupby(groupCol)[valueCol].sum().sort_values(ascending=False)
            total = grouped.sum()
            if total == 0:
                return []
            top = grouped.head(topN)
            return [
                {
                    "group": str(name),
                    "value": round(float(val), 4),
                    "share": round(float(val / total), 4),
                }
                for name, val in top.items()
            ]
        except Exception:
            return []

    @staticmethod
    def computeCorrelations(df: pd.DataFrame, valueCols: list[str]) -> dict:
        """
        Computes pairwise Pearson correlations with sample-size guardrails.

        Skips computation when fewer than 10 valid observations are available.

        Args:
            df (pd.DataFrame): Source data.
            valueCols (list[str]): Numeric columns to correlate.

        Returns:
            dict: Correlation matrix as nested dict, or skip reason.
        """
        MIN_SAMPLES = 10
        numericCols = [c for c in valueCols if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
        if len(numericCols) < 2:
            return {"skipped": True, "reason": "fewer than 2 numeric columns available"}
        validCount = df[numericCols].dropna().shape[0]
        if validCount < MIN_SAMPLES:
            return {"skipped": True, "reason": f"only {validCount} valid rows (minimum {MIN_SAMPLES})"}
        try:
            corrMatrix = df[numericCols].corr()
            pairs = []
            seen = set()
            for i, colA in enumerate(numericCols):
                for j, colB in enumerate(numericCols):
                    if i >= j:
                        continue
                    key = (colA, colB)
                    if key in seen:
                        continue
                    seen.add(key)
                    val = corrMatrix.loc[colA, colB]
                    if pd.notna(val):
                        pairs.append({
                            "columnA": colA,
                            "columnB": colB,
                            "pearsonR": round(float(val), 4),
                        })
            return {"skipped": False, "pairs": pairs, "sampleSize": validCount}
        except Exception:
            return {"skipped": True, "reason": "correlation computation failed"}

    @staticmethod
    def _coerceNumeric(df: pd.DataFrame) -> pd.DataFrame:
        """
        Converts columns that are predominantly numeric into numeric dtype.

        Widget payloads arrive as JSON, so numeric columns often carry strings.
        A column converts only when at least 80% of its values parse, which
        keeps genuinely categorical columns categorical.
        """
        converted = df.copy()
        for column in converted.columns:
            if pd.api.types.is_numeric_dtype(converted[column]):
                continue
            numeric = pd.to_numeric(converted[column], errors="coerce")
            if numeric.notna().sum() >= max(1, int(len(converted) * 0.8)):
                converted[column] = numeric
        return converted

    @staticmethod
    def _inferDateColumn(df: pd.DataFrame) -> str | None:
        """Heuristically identifies a date/time column in the DataFrame."""
        for col in df.columns:
            if df[col].dtype.kind == "M":
                return col
        dateKeywords = ["date", "time", "month", "year", "day", "period", "week", "quarter"]
        for col in df.columns:
            if any(kw in col.lower() for kw in dateKeywords):
                try:
                    pd.to_datetime(df[col], errors="raise")
                    return col
                except Exception:
                    continue
        return None

    def buildWidgetSignals(self, widget: dict) -> dict:
        """
        Computes statistical signals for a single widget.

        All signals are scoped to this widget's own data. Correlations are only
        computed between columns of the same widget, never across widgets, since
        rows of different widgets share no index.

        Args:
            widget (dict): Widget config with `chartType` and `data`.

        Returns:
            dict: Signals with keys periodDeltas, anomalies, concentration,
                topContributors, correlations. Empty dict when the widget
                carries no tabular data.
        """
        normalized = normalizeWidgetData(widget)
        if normalized.get("kind") not in ("series", "records", "matrix", "points"):
            return {}
        rows = normalized.get("rows") or []
        if not rows:
            return {}

        try:
            df = self._coerceNumeric(pd.DataFrame(rows, columns=normalized["columns"]))
        except Exception:
            return {}
        if df.empty:
            return {}

        numericCols = df.select_dtypes(include=[np.number]).columns.tolist()
        categoricalCols = df.select_dtypes(include=["object", "category"]).columns.tolist()
        dateCol = self._inferDateColumn(df)

        signals = {
            "periodDeltas": [],
            "anomalies": [],
            "concentration": {},
            "topContributors": [],
            "correlations": {},
        }

        if dateCol and numericCols:
            signals["periodDeltas"] = self.computePeriodDeltas(df, dateCol, numericCols)
        if numericCols:
            signals["anomalies"] = self.detectAnomalies(df, numericCols)
            signals["correlations"] = self.computeCorrelations(df, numericCols)
        if categoricalCols and numericCols:
            groupCol, valueCol = categoricalCols[0], numericCols[0]
            signals["concentration"] = self.computeConcentration(df, groupCol, valueCol)
            signals["topContributors"] = self.computeTopContributors(df, groupCol, valueCol)

        return signals

    def buildStatisticalSummary(self, chartData: list[dict]) -> dict:
        """
        Computes per-widget statistical signals for every widget on the page.

        Args:
            chartData (list[dict]): Widget dicts from InsightContextBuilder.

        Returns:
            dict: `{"widgetCount": int, "perWidget": {ref: signals}}` where ref
                is the widget's `ref`, falling back to `id`, then position.
        """
        try:
            logger.info(f"Building per-widget signals for {len(chartData)} widgets.")
            perWidget = {}
            for index, widget in enumerate(chartData):
                ref = widget.get("ref") or widget.get("id") or f"W{index + 1}"
                signals = self.buildWidgetSignals(widget)
                if signals:
                    perWidget[str(ref)] = signals
            return {"widgetCount": len(chartData), "perWidget": perWidget}
        except Exception as e:
            logger.warning(f"SignalEngine summary partially failed: {e}")
            return {"widgetCount": len(chartData), "perWidget": {}}


def formatWidgetSignals(signals: dict) -> str | None:
    """
    Renders a widget's signals as one compact line for the LLM payload.

    Args:
        signals (dict): Output of `SignalEngine.buildWidgetSignals`.

    Returns:
        str | None: e.g. "change +50.0% on revenue; top3Share=0.96, HHI=0.53",
            or None when there is nothing worth stating.
    """
    if not signals:
        return None

    parts = []
    for delta in signals.get("periodDeltas") or []:
        if delta.get("pctChange") is not None:
            parts.append(f"change {delta['pctChange']:+}% on {delta['column']}")

    for anomaly in signals.get("anomalies") or []:
        parts.append(
            f"{anomaly['outlierCount']} anomalies in {anomaly['column']} "
            f"of {anomaly['totalRows']} rows (median {anomaly['median']})"
        )

    concentration = signals.get("concentration") or {}
    if concentration:
        parts.append(
            f"top3Share={concentration['top3Share']}, HHI={concentration['herfindahlIndex']} "
            f"across {concentration['numGroups']} groups"
        )

    correlations = signals.get("correlations") or {}
    for pair in correlations.get("pairs") or []:
        if abs(pair["pearsonR"]) >= 0.5:
            parts.append(f"r({pair['columnA']},{pair['columnB']})={pair['pearsonR']}")

    return "; ".join(parts) if parts else None
