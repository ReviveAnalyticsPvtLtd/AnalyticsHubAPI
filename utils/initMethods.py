"""
initMethods.py

This module provides utility functions for data serialization, fetching data from Redis/parquet, and preparing data for charting and analytics.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["fetch_data", "serializer"]


import pandas as pd
import numpy as np
import datetime
import redis
import io
import os

def serializer(obj):
    """
    Serializes various data types (NumPy, pandas, datetime, etc.) to JSON-compatible formats.

    Args:
        obj: The object to serialize.

    Returns:
        JSON-compatible representation of the object.
    """
    if isinstance(obj, (np.integer)):
        return obj.item()  
    elif isinstance(obj, (np.floating)):
        if np.isnan(obj) or np.isinf(obj):
            return None  
        return obj.item()  
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.datetime64):
        return str(obj)  
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")  
    elif isinstance(obj, pd.Series):
        return obj.tolist()  
    elif isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()  
    elif isinstance(obj, (set, tuple)):
        return list(obj)
    elif isinstance(obj, complex):
        return {"real": obj.real, "imag": obj.imag}

def fetch_data(projectId: str, tableName: str, baseFilters: list = list()):
    """
    Fetches a DataFrame from Redis cache or parquet file, with optional filtering.

    Args:
        projectId (str): The project ID.
        tableName (str): The table name.
        baseFilters (list, optional): List of filter conditions to apply.

    Returns:
        pd.DataFrame: The resulting DataFrame after applying filters.
    """
    r = redis.Redis(host=os.environ["REDIS_HOST"], port=int(os.environ["REDIS_PORT"]), password=os.environ["REDIS_PASSWORD"])
    key = f"{projectId}::{tableName}"
    df = r.get(key)
    if df is None:
        buffer = io.BytesIO()
        df = pd.read_parquet(os.environ["FILE_URL"].format(projectId = projectId, fileName = tableName))
        df.to_parquet(buffer, compression = "snappy")
        r.set(name = key, value = buffer.getvalue(), ex = 60)
    else:
        df = pd.read_parquet(io.BytesIO(df))

    if baseFilters:
        for filter in baseFilters:
            for column, condition in filter.items():
                columnTable, column = column.split(".")
                if columnTable == tableName: 
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
                        continue
                    else:
                        df = df[df[column] == condition]
                        continue
                else:
                    continue
    return df
