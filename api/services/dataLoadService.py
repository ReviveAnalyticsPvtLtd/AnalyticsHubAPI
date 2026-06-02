"""
dataLoadService.py

dataLoadService module provides services for loading and managing data from various sources (CSV, Excel, PDF, MySQL, PostgreSQL, MongoDB) and deleting tables for NubrixAI projects.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["dataLoadService"] 


from nubrix.components.pdfTableExtractor import PdfTableExtractor
from api.commons import updateProjectModifiedAt
from utils.exceptionHandler import CustomException
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from fastapi import Form, UploadFile
from sqlalchemy import create_engine
from urllib.request import urlopen
from utils.logger import logger
from api.commons import client
from typing import Annotated
from api.models import (
    LoadMySQLorPostgreSQL,
    LoadMongoDB,
    DeleteTable
)
import pandas as pd
import tempfile
import json
import fitz
import time
import io
import os
import gc
import re
import csv

class DataLoadService:
    """
    Service class for loading data from different sources and managing project tables.
    """
    def __init__(self) -> None:
        """
        Initializes the DataLoadService and sets up the client for database operations.
        """
        logger.info("Initializing Data Load Service.")
        self.client = client

    def _sanitizeFileName(self, fileName: str) -> str:
        """
        Sanitize a file name by removing the extension and replacing spaces and special characters with underscores.

        Args:
            fileName (str): The original file name (with or without extension).

        Returns:
            str: A sanitized, storage-safe file name without the extension.
        """
        baseName = os.path.splitext(fileName)[0]
        sanitized = re.sub(r"[^\w-]", "_", baseName)
        sanitized = re.sub(r"_+", "_", sanitized)
        sanitized = sanitized.strip("_")
        return sanitized

    @staticmethod
    def _sanitizeDfForParquet(df: pd.DataFrame) -> pd.DataFrame:
        for col in df.columns:
            if df[col].dtype == object:
                originalNulls = df[col].isna()
                numeric = pd.to_numeric(df[col], errors="coerce")
                newNulls = numeric.isna() & ~originalNulls
                if not newNulls.any():
                    df[col] = numeric
                else:
                    df[col] = (
                        df[col]
                        .astype(str)
                        .replace({"None": pd.NA, "nan": pd.NA, "<NA>": pd.NA})
                    )
        return df

    @staticmethod
    def _detectCsvDelimiter(fileBytes: bytes) -> str:
        sample = fileBytes[:8192].decode("utf-8-sig", errors="ignore")
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            return ","

    @classmethod
    def _readCsvUpload(cls, fileBytes: bytes) -> pd.DataFrame:
        delimiter = cls._detectCsvDelimiter(fileBytes)
        return pd.read_csv(io.BytesIO(fileBytes), sep=delimiter, parse_dates=True)

    async def loadCsvData(self, projectId: Annotated[str, Form()], files: list[UploadFile]) -> None:
        """
        Load CSV files into project storage.
        Raises:
            CustomException:
                400 - Missing projectId or files
                415 - Unsupported file type
                422 - Invalid CSV upload details
                500 - CSV upload failed
        """
        try:
            if not projectId or not files:
                raise CustomException(
                    ValueError("Missing projectId or files"),
                    statusCode=400,
                    uiMessage="Missing projectId or files."
                )
            for file in files:
                if not file.filename.lower().endswith(".csv"):
                    raise CustomException(
                        ValueError("Invalid file type"),
                        statusCode=415,
                        uiMessage="Unsupported file type. Upload CSV files only."
                    )
                with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                    df = self._readCsvUpload(await file.read())
                    self._sanitizeDfForParquet(df).to_parquet(temp.name, compression="snappy")
                    sanitizedName = self._sanitizeFileName(file.filename)
                    self.client.storage.from_("NubrixAI").upload(
                        file=temp.name,
                        path=f"{projectId}/{sanitizedName}.parquet",
                        file_options={"upsert": "true"}
                    )
            updateProjectModifiedAt(projectId)
            return
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="CSV upload failed. Try again later."
            )
            logger.error(exception)
            raise exception
        
    async def loadExcelData(self, projectId: Annotated[str, Form()], files: list[UploadFile]) -> None:
        """
        Load Excel files into project storage.
        Raises:
            CustomException:
                400 - Missing projectId or files
                415 - Unsupported file type
                422 - Invalid Excel upload details
                500 - Excel upload failed
        """
        try:
            if not projectId or not files:
                raise CustomException(
                    ValueError("Missing projectId or files"),
                    statusCode=400,
                    uiMessage="Missing projectId or files."
                )
            for file in files:
                if not file.filename.lower().endswith((".xls", ".xlsx")):
                    raise CustomException(
                        ValueError("Invalid file type"),
                        statusCode=415,
                        uiMessage="Unsupported file type. Upload Excel files only."
                    )
                allSheetData = pd.read_excel(io.BytesIO(await file.read()), sheet_name=None, parse_dates=True)
                sanitizedBase = self._sanitizeFileName(file.filename)
                for sheetName, sheetData in allSheetData.items():
                    sanitizedSheet = re.sub(r"[^\w-]", "_", str(sheetName)).strip("_")
                    with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                        sheetData.to_parquet(temp.name, compression="snappy")
                        fileName = f"{sanitizedBase}_{sanitizedSheet}.parquet"
                        self.client.storage.from_("NubrixAI").upload(
                            file=temp.name,
                            path=f"{projectId}/{fileName}",
                            file_options={"upsert": "true"}
                        )
            updateProjectModifiedAt(projectId)
            return
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Excel upload failed. Try again later."
            )
            logger.error(exception)
            raise exception
        
    async def loadPdfData(self, projectId: Annotated[str, Form()], files: list[UploadFile]) -> None:
        """
        Load PDF files into project storage using a two-pass approach:
        Pass 1 rasterizes each page and extracts table fragments via a VLM.
        Pass 2 sends a compact summary of all fragments to the LLM which
        returns a merge plan grouping them into logical tables.
        Tables are stored as individual parquet files ({filename}_table{N}.parquet).
        Raises:
            CustomException:
                400 - Missing projectId or files
                415 - Unsupported file type
                422 - No extractable tables found
                500 - PDF upload failed
        """
        try:
            if not projectId or not files:
                raise CustomException(
                    ValueError("Missing projectId or files"),
                    statusCode=400,
                    uiMessage="Missing projectId or files."
                )
            for file in files:
                if not file.filename.lower().endswith(".pdf"):
                    raise CustomException(
                        ValueError("Invalid file type"),
                        statusCode=415,
                        uiMessage="Unsupported file type. Upload PDF files only."
                    )
                fileBytes = await file.read()
                baseName = self._sanitizeFileName(file.filename)
                extractor = PdfTableExtractor()

                pageImages: list[tuple[int, str]] = []
                with fitz.open(stream=fileBytes, filetype="pdf") as doc:
                    totalPages = len(doc)
                    logger.info(f"Processing {file.filename}: {totalPages} pages")

                    for pageNum, page in enumerate(doc, start=1):
                        b64 = PdfTableExtractor.renderPageToBase64(
                            page, dpi=extractor.dpi
                        )
                        pageImages.append((pageNum, b64))

                del fileBytes
                gc.collect()

                fragments = await extractor.extractPagesParallel(
                    pageImages, totalPages
                )
                del pageImages
                gc.collect()

                if not fragments:
                    raise CustomException(
                        ValueError("No extractable tables in PDF"),
                        statusCode=422,
                        uiMessage="No extractable tables found in the PDF. Ensure the PDF contains tables."
                    )

                mergePlan = extractor.planMerge(fragments)
                pendingTables: list[dict] = []

                for group in mergePlan.groups:
                    canonicalHeaders = group.canonicalHeaders
                    numCols = len(canonicalHeaders)
                    dfs = []
                    for idx in group.tableIndices:
                        if idx < 0 or idx >= len(fragments):
                            continue
                        rows = fragments[idx]["table"].rows
                        normalized = []
                        for r in rows:
                            if len(r) == numCols:
                                normalized.append(r)
                            elif len(r) > numCols:
                                normalized.append(r[:numCols])
                            else:
                                normalized.append(
                                    r + [None] * (numCols - len(r))
                                )
                        dfs.append(
                            pd.DataFrame(normalized, columns=canonicalHeaders)
                        )

                    if not dfs:
                        continue
                    combined = pd.concat(dfs, ignore_index=True)
                    combined.dropna(how="all", inplace=True)
                    if not combined.empty:
                        pendingTables.append({
                            "headers": canonicalHeaders,
                            "df": combined,
                        })

                if not pendingTables:
                    raise CustomException(
                        ValueError("No extractable tables in PDF"),
                        statusCode=422,
                        uiMessage="No extractable tables found in the PDF. Ensure the PDF contains tables."
                    )

                logger.info(
                    f"{file.filename}: {len(fragments)} fragments merged "
                    f"into {len(pendingTables)} table(s)"
                )

                for idx, entry in enumerate(pendingTables, start=1):
                    with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                        self._sanitizeDfForParquet(entry["df"]).to_parquet(
                            temp.name, compression="snappy"
                        )
                        fileName = f"{baseName}_table{idx}.parquet"
                        self.client.storage.from_("NubrixAI").upload(
                            file=temp.name,
                            path=f"{projectId}/{fileName}",
                            file_options={"upsert": "true"}
                        )

            updateProjectModifiedAt(projectId)
            return
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="PDF upload failed. Try again later."
            )
            logger.error(exception)
            raise exception

    def loadMySql(self, connection: LoadMySQLorPostgreSQL) -> None:
        """
        Load data from MySQL database.
        Raises:
            CustomException:
                400 - Missing required DB connection fields
                400 - Unable to connect to MySQL
                422 - Invalid connection details
                500 - MySQL data load failed
        """
        try:
            required = [connection.host, connection.port, connection.user,
                        connection.password, connection.db, connection.table, connection.projectId]
            if not all(required):
                raise CustomException(
                    ValueError("Missing required DB fields"),
                    statusCode=400,
                    uiMessage="Missing required DB connection fields."
                )
            connStr = f"mysql+pymysql://{connection.user}:{connection.password}@{connection.host}:{connection.port}/{connection.db}"
            engine = create_engine(connStr)
            sanitizedTable = self._sanitizeFileName(connection.table)
            with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                pd.read_sql(f"SELECT * FROM {connection.table}", engine).to_parquet(
                    temp.name, compression="snappy"
                )
                self.client.storage.from_("NubrixAI").upload(
                    file=temp.name,
                    path=f"{connection.projectId}/{sanitizedTable}.parquet",
                    file_options={"upsert": "true"}
                )
            updateProjectModifiedAt(connection.projectId)
            return
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                statusCode=400,
                uiMessage="Unable to connect to MySQL : check host/port/credentials."
            )
            logger.error(exception)
            raise exception

            
    def loadPostgreSQL(self, connection: LoadMySQLorPostgreSQL) -> None:
        """
        Load data from PostgreSQL database.
        Raises:
            CustomException:
                400 - Missing required DB connection fields
                400 - Unable to connect to PostgreSQL
                422 - Invalid connection details
                500 - PostgreSQL data load failed
        """
        try:
            required = [connection.host, connection.port, connection.user,
                        connection.password, connection.db, connection.table, connection.projectId]
            if not all(required):
                raise CustomException(
                    ValueError("Missing required DB fields"),
                    statusCode=400,
                    uiMessage="Missing required DB connection fields."
                )
            connStr = f"postgresql+psycopg2://{connection.user}:{connection.password}@{connection.host}:{connection.port}/{connection.db}"
            engine = create_engine(connStr)
            sanitizedTable = self._sanitizeFileName(connection.table)
            with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                pd.read_sql(f"SELECT * FROM {connection.table}", engine).to_parquet(
                    temp.name, compression="snappy"
                )
                self.client.storage.from_("NubrixAI").upload(
                    file=temp.name,
                    path=f"{connection.projectId}/{sanitizedTable}.parquet",
                    file_options={"upsert": "true"}
                )
            updateProjectModifiedAt(connection.projectId)
            return
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                statusCode=400,
                uiMessage="Unable to connect to PostgreSQL: check host/port/credentials."
            )
            logger.error(exception)
            raise exception
        
    def loadMongoDB(self, connection: LoadMongoDB) -> None:
        """
        Load data from MongoDB.
        Raises:
            CustomException:
                400 - Missing required DB connection fields
                400 - Unable to connect to MongoDB
                422 - Invalid MongoDB connection details
                500 - MongoDB data load failed
        """
        try:
            if not connection.connectionString or not connection.db or not connection.collection:
                raise CustomException(
                    ValueError("Missing MongoDB connection fields"),
                    statusCode=400,
                    uiMessage="Missing required DB connection fields."
                )
            with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                mongoClient = MongoClient(connection.connectionString, server_api=ServerApi('1'))
                records = list(mongoClient[connection.db][connection.collection].find())
                for record in records:
                    record.pop("_id", None)
                pd.DataFrame(records).to_parquet(temp.name, compression="snappy")
                sanitizedCollection = self._sanitizeFileName(connection.collection)
                self.client.storage.from_("NubrixAI").upload(
                    file=temp.name,
                    path=f"{connection.projectId}/{sanitizedCollection}.parquet",
                    file_options={"upsert": "true"}
                )
            updateProjectModifiedAt(connection.projectId)
            return
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                statusCode=400,
                uiMessage="Unable to connect to MongoDB: check connection string/credentials."
            )
            logger.error(exception)
            raise exception
        
    def deleteTable(self, tableDetails: DeleteTable) -> None:
        """
        Deletes a table from the project storage and updates project metadata accordingly.

        Args:
            tableDetails (DeleteTable): Details of the table to delete.
        Returns:
            None
        Raises:
            CustomException: If deletion or metadata update fails.
        """
        try:
            _ = self.client.storage.from_("NubrixAI").remove(f"{tableDetails.projectId}/{tableDetails.tableName}" + ".parquet")
            fileUrl = os.environ["FILE_URL"].format(projectId = tableDetails.projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            jsonData = json.loads(urlopen(fileUrl).read())
            jsonData.pop(tableDetails.tableName)
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(jsonData, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("NubrixAI").upload(path = f"{tableDetails.projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            updateProjectModifiedAt(tableDetails.projectId)
            return
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception

dataLoadService = DataLoadService()
