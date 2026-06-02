"""
signalEngine.py

Deterministic statistical signal extraction for the hybrid insight pipeline.
Computes period deltas, anomaly detection, concentration indices, top contributors,
and pairwise correlations from chart data, producing a compact statistical summary
consumed by the LLM prompt.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["SignalEngine"]


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

    def buildStatisticalSummary(self, chartData: list[dict]) -> dict:
        """
        Orchestrates all signal computations over the provided chart data.

        Infers DataFrame structure from widget chart data dicts and runs
        applicable statistical functions.

        Args:
            chartData (list[dict]): List of widget chart data dicts from InsightContextBuilder.

        Returns:
            dict: Compact statistical summary for LLM consumption.
        """
        try:
            logger.info(f"Building statistical summary for {len(chartData)} widgets.")
            summary = {
                "periodDeltas": [],
                "anomalies": [],
                "concentration": [],
                "topContributors": [],
                "correlations": {},
                "widgetCount": len(chartData),
            }

            allNumericCols = []
            for widget in chartData:
                data = widget.get("data")
                if not data:
                    continue

                df = self._chartDataToDataFrame(data, widget)
                if df is None or df.empty:
                    continue

                numericCols = df.select_dtypes(include=[np.number]).columns.tolist()
                categoricalCols = df.select_dtypes(include=["object", "category"]).columns.tolist()
                dateCol = self._inferDateColumn(df)

                if dateCol and numericCols:
                    deltas = self.computePeriodDeltas(df, dateCol, numericCols)
                    summary["periodDeltas"].extend(deltas)

                if numericCols:
                    anomalies = self.detectAnomalies(df, numericCols)
                    summary["anomalies"].extend(anomalies)
                    allNumericCols.extend(numericCols)

                if categoricalCols and numericCols:
                    groupCol = categoricalCols[0]
                    valueCol = numericCols[0]
                    concentration = self.computeConcentration(df, groupCol, valueCol)
                    if concentration:
                        summary["concentration"].append(concentration)
                    contributors = self.computeTopContributors(df, groupCol, valueCol)
                    if contributors:
                        summary["topContributors"].append({
                            "widgetTitle": widget.get("title", "unknown"),
                            "contributors": contributors,
                        })

            if allNumericCols and len(set(allNumericCols)) >= 2:
                mergedDf = self._mergeAllWidgetData(chartData)
                if mergedDf is not None and not mergedDf.empty:
                    uniqueNumeric = list(dict.fromkeys(allNumericCols))
                    summary["correlations"] = self.computeCorrelations(mergedDf, uniqueNumeric)

            logger.info("Statistical summary built successfully.")
            return summary
        except Exception as e:
            logger.warning(f"SignalEngine summary partially failed: {e}")
            return {"periodDeltas": [], "anomalies": [], "concentration": [], "topContributors": [], "correlations": {}, "widgetCount": len(chartData)}

    @staticmethod
    def _chartDataToDataFrame(data: dict | list | str, widget: dict) -> pd.DataFrame | None:
        """Attempts to convert widget chart data into a DataFrame."""
        try:
            if isinstance(data, list):
                return pd.DataFrame(data)
            if isinstance(data, dict):
                datasets = data.get("datasets")
                labels = widget.get("xLabels") or data.get("labels")
                if datasets and labels:
                    records = {}
                    records["label"] = labels
                    for ds in datasets:
                        dsLabel = ds.get("label", "value")
                        dsData = ds.get("data", [])
                        records[dsLabel] = dsData
                    return pd.DataFrame(records)
                return pd.DataFrame([data])
            if isinstance(data, str):
                parsed = pd.read_json(data)
                return parsed
        except Exception:
            pass
        return None

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

    @staticmethod
    def _mergeAllWidgetData(chartData: list[dict]) -> pd.DataFrame | None:
        """Collects all numeric columns from all widgets into a single DataFrame (column-wise concat)."""
        frames = []
        for widget in chartData:
            data = widget.get("data")
            if not data:
                continue
            df = SignalEngine._chartDataToDataFrame(data, widget)
            if df is not None and not df.empty:
                numericOnly = df.select_dtypes(include=[np.number])
                if not numericOnly.empty:
                    frames.append(numericOnly)
        if not frames:
            return None
        try:
            return pd.concat(frames, axis=1)
        except Exception:
            return None
