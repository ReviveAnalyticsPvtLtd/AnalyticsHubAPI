"""
fetch_data.py

Self-contained fetch_data helper for sandbox child processes.
Mirrors the behavior of utils/initMethods.py fetch_data but is independent
of the main backend package tree.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["fetch_data"]

import io
import os

import pandas as pd
import redis


def fetch_data(project_id: str, table_name: str, base_filters: list = None, *args) -> pd.DataFrame:
    """
    Fetch a DataFrame from Redis cache or parquet file, with optional filtering.

    This is a sandbox-local copy of the main backend's fetch_data.
    The projectId is partially applied by child_entry.py, so generated code
    calls fetch_data("tableName", ...) but the child receives it as
    fetch_data(projectId, tableName, ...).
    """
    if base_filters is None:
        base_filters = []

    for arg in args:
        if isinstance(arg, list):
            base_filters.extend(arg)

    r = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD", ""),
    )

    key = f"{project_id}::{table_name}"
    df_bytes = r.get(key)

    if df_bytes is None:
        file_url = os.environ.get("FILE_URL", "")
        url = file_url.format(projectId=project_id, fileName=table_name)
        df = pd.read_parquet(url)
        buffer = io.BytesIO()
        df.to_parquet(buffer, compression="snappy")
        r.set(name=key, value=buffer.getvalue(), ex=60)
    else:
        df = pd.read_parquet(io.BytesIO(df_bytes))

    if base_filters:
        for filt in base_filters:
            for column_key, condition in filt.items():
                column_table, column = column_key.split(".")
                if column_table != table_name:
                    continue
                if column not in df.columns:
                    continue

                if isinstance(condition, dict):
                    if df[column].dtype == "object":
                        if "contains" in condition:
                            df = df[df[column].str.contains(condition["contains"], case=False, na=False)]
                            continue
                        if "startswith" in condition:
                            df = df[df[column].str.startswith(condition["startswith"], na=False)]
                            continue
                        if "endswith" in condition:
                            df = df[df[column].str.endswith(condition["endswith"], na=False)]
                            continue
                    if "min" in condition:
                        df = df[df[column] >= condition["min"]]
                        continue
                    if "max" in condition:
                        df = df[df[column] <= condition["max"]]
                        continue

                if isinstance(condition, (list, tuple, set)):
                    df = df[df[column].isin(condition)]
                else:
                    df = df[df[column] == condition]

    return df
