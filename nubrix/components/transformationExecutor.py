"""
transformationExecutor.py

This module executes generated transformation code and persists approved
transformed tables.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["TransformationExecutor"]


from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import redirect_stderr, redirect_stdout
from utils.exceptionHandler import CustomException
from utils.initMethods import fetch_data, serializer
from api.commons import client
from utils.logger import logger
import pandas as pd
import numpy as np
import datetime
import redis
import json
import math
import io
import os
import re


class TransformationExecutor:
    """
    Execute pandas transformation code and persist approved outputs.
    """
    def __init__(self, timeoutSeconds: int = 30):
        """Initialize the executor."""
        self.timeoutSeconds = timeoutSeconds
        self.client = client

    def _redis_client(self) -> redis.Redis:
        """Create a Redis client using environment credentials."""
        return redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            password=os.environ["REDIS_PASSWORD"],
        )

    def _validate_table_name(self, tableName: str) -> str:
        """Validate the output table name."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", tableName):
            raise ValueError("Table name must start with a letter and contain only letters, numbers, hyphens, and underscores.")
        return tableName

    def _restricted_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        """Allow only transformation-safe imports."""
        allowedModules = {
            "pandas", "numpy", "datetime", "math", "re", "time",
            "sklearn", "scipy", "statsmodels"
        }
        rootName = name.split(".")[0]
        if rootName not in allowedModules:
            raise ImportError(f"Import '{name}' is not allowed in transformation code.")
        return __import__(name, globals, locals, fromlist, level)

    def _safe_builtins(self) -> dict:
        """Return a constrained builtins map for generated code."""
        import builtins
        # Start with all standard builtins
        safe = {k: v for k, v in builtins.__dict__.items()}
        # Restrict dangerous system-access operations to ensure sandbox integrity
        restricted = {"open", "compile", "eval", "exec", "globals", "locals", "memoryview", "input"}
        for key in restricted:
            safe.pop(key, None)
        # Apply restricted import validator
        safe["__import__"] = self._restricted_import
        return safe

    def _execute_code(self, projectId: str, pythonCode: str) -> pd.DataFrame:
        """Execute generated code and return `final_df`."""
        stdoutBuffer = io.StringIO()
        stderrBuffer = io.StringIO()
        executionGlobals = {
            "__builtins__": self._safe_builtins(),
            "datetime": datetime,
            "fetch_data": fetch_data,
            "math": math,
            "np": np,
            "pd": pd,
            "projectId": projectId,
            "serializer": serializer,
        }
        with redirect_stdout(stdoutBuffer), redirect_stderr(stderrBuffer):
            exec(pythonCode, executionGlobals)
        finalDf = executionGlobals.get("final_df")
        if finalDf is None:
            raise ValueError("Transformation code must create a final_df variable.")
        if not isinstance(finalDf, pd.DataFrame):
            raise ValueError("final_df must be a pandas DataFrame.")
        return finalDf

    def executeAndPreview(self, projectId: str, pythonCode: str, tableName: str) -> tuple[list[dict], bytes]:
        """
        Execute code and return preview rows plus parquet bytes.
        """
        try:
            self._validate_table_name(tableName)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._execute_code, projectId, pythonCode)
                try:
                    finalDf = future.result(timeout=self.timeoutSeconds)
                except FuturesTimeoutError as e:
                    raise TimeoutError(f"Transformation execution exceeded {self.timeoutSeconds} seconds.") from e
            previewRecords = finalDf.head(10).replace({np.nan: None}).to_dict(orient="records")
            previewRows = json.loads(json.dumps(previewRecords, default=serializer))
            parquetBuffer = io.BytesIO()
            finalDf.to_parquet(parquetBuffer, compression="snappy")
            return previewRows, parquetBuffer.getvalue()
        except Exception as e:
            exception = CustomException(e, statusCode=400, uiMessage=str(e))
            logger.error(exception)
            raise exception

    def apply(self, projectId: str, parquetBytes: bytes, tableName: str) -> None:
        """
        Upload parquet bytes to Supabase storage and refresh the fetch_data cache.
        """
        try:
            self._validate_table_name(tableName)
            storagePath = f"{projectId}/{tableName}.parquet"
            self.client.storage.from_("AnalyticsHub").upload(
                path=storagePath,
                file=parquetBytes,
                file_options={"upsert": "true"},
            )
            redisClient = self._redis_client()
            redisClient.set(name=f"{projectId}::{tableName}", value=parquetBytes, ex=300)
        except Exception as e:
            exception = CustomException(e, statusCode=500, uiMessage="Failed to apply transformation.")
            logger.error(exception)
            raise exception
