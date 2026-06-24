"""
serializer.py

Self-contained serializer helper for sandbox child processes.
Mirrors the behavior of utils/initMethods.py serializer but is independent
of the main backend package tree.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["serializer"]

import datetime

import numpy as np
import pandas as pd


def serializer(obj):
    """
    Serialize various data types (NumPy, pandas, datetime, etc.) to JSON-compatible formats.
    Used as the default= argument in json.dumps within generated code.
    """
    if isinstance(obj, np.integer):
        return obj.item()
    elif isinstance(obj, np.floating):
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
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
