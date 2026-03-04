"""
dataLoadService.py

dataLoadService module provides services for loading and managing data from various sources (CSV, Excel, PDF, MySQL, PostgreSQL, MongoDB) and deleting tables for AnalyticsHub projects.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["dataLoadService"] 


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
from analyticsHub.components.pdfOcrExtractor import PdfOcrExtractor
import pdfplumber
import pandas as pd
import tempfile
import json
import fitz  
import time
import io
import os
import re

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
                    pd.read_csv(io.BytesIO(await file.read()), parse_dates=True).to_parquet(
                        temp.name, compression="snappy"
                    )
                    sanitizedName = self._sanitizeFileName(file.filename)
                    self.client.storage.from_("AnalyticsHub").upload(
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
                        self.client.storage.from_("AnalyticsHub").upload(
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
        Load PDF files into project storage. Extracts tables and text content.
        Tables spanning multiple pages are automatically merged when their header
        signatures match across consecutive pages.
        Tables are stored as individual parquet files ({filename}_table{N}.parquet).
        A context timeline preserving the PDF's reading order is stored as a
        structured DataFrame ({filename}_text.parquet) with columns:
        page_number, row_order, context_type, content, table_ref.
        context_type is 'narrative' for text blocks, 'table_anchor' for new table
        positions, or 'table_continuation' for tables spanning from a previous page.
        table_ref links anchor/continuation rows to the corresponding table file name.
        Raises:
            CustomException:
                400 - Missing projectId or files
                415 - Unsupported file type
                422 - No extractable content found
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
                contextRows = []
                pendingTables = []

                ocrExtractor = None

                with fitz.open(stream=fileBytes, filetype="pdf") as fitzDoc, \
                     pdfplumber.open(io.BytesIO(fileBytes)) as pdf:
                    for pageNum, page in enumerate(pdf.pages, start=1):
                        contextRowsBefore = len(contextRows)
                        pendingTablesBefore = len(pendingTables)

                        detectedTables = page.find_tables()
                        sortedTableEntries = sorted(
                            [(t.bbox, t.extract()) for t in detectedTables],
                            key=lambda x: x[0][1]
                        )
                        tableBboxes = [entry[0] for entry in sortedTableEntries]
                        pageWidth = float(page.width)
                        pageHeight = float(page.height)
                        currentY = 0.0

                        for bbox, tableData in sortedTableEntries:
                            tableTop = float(bbox[1])
                            tableBottom = float(bbox[3])

                            if tableTop - currentY > 1:
                                try:
                                    cropped = page.crop((0, currentY, pageWidth, tableTop))
                                    text = cropped.extract_text()
                                    if text and text.strip():
                                        contextRows.append({
                                            "page_number": pageNum,
                                            "row_order": len(contextRows) + 1,
                                            "context_type": "narrative",
                                            "content": text.strip(),
                                            "table_ref": None
                                        })
                                except Exception:
                                    pass

                            if not tableData or len(tableData) < 2:
                                currentY = tableBottom
                                continue
                            headers = [
                                str(h).strip() if h and str(h).strip() else f"column_{i}"
                                for i, h in enumerate(tableData[0])
                            ]
                            seen = {}
                            uniqueHeaders = []
                            for h in headers:
                                if h in seen:
                                    seen[h] += 1
                                    uniqueHeaders.append(f"{h}_{seen[h]}")
                                else:
                                    seen[h] = 0
                                    uniqueHeaders.append(h)

                            signature = "|".join(
                                col.lower().strip() for col in uniqueHeaders
                            )

                            dataRows = [
                                row for row in tableData[1:]
                                if [str(c).strip().lower() if c else "" for c in row] != [col.lower().strip() for col in uniqueHeaders]
                            ]
                            df = pd.DataFrame(dataRows, columns=uniqueHeaders)
                            df.dropna(how="all", inplace=True)
                            df.reset_index(drop=True, inplace=True)
                            if df.empty:
                                currentY = tableBottom
                                continue

                            merged = False
                            pendingIdx = None
                            for i, pending in enumerate(pendingTables):
                                if pending["signature"] == signature and pending["lastPage"] == pageNum - 1:
                                    pending["df"] = pd.concat(
                                        [pending["df"], df], ignore_index=True
                                    )
                                    pending["lastPage"] = pageNum
                                    merged = True
                                    pendingIdx = i
                                    break

                            if not merged:
                                pendingTables.append({
                                    "signature": signature,
                                    "headers": uniqueHeaders,
                                    "df": df,
                                    "lastPage": pageNum
                                })
                                pendingIdx = len(pendingTables) - 1

                            contextType = "table_continuation" if merged else "table_anchor"
                            anchorContent = (
                                f"[Table continued: {', '.join(uniqueHeaders)}]"
                                if merged else
                                f"[Table: {', '.join(uniqueHeaders)}]"
                            )
                            contextRows.append({
                                "page_number": pageNum,
                                "row_order": len(contextRows) + 1,
                                "context_type": contextType,
                                "content": anchorContent,
                                "table_ref": pendingIdx
                            })

                            currentY = tableBottom

                        remainingHeight = pageHeight - currentY
                        if remainingHeight > 1:
                            try:
                                if tableBboxes:
                                    cropped = page.crop((0, currentY, pageWidth, pageHeight))
                                    text = cropped.extract_text()
                                else:
                                    text = page.extract_text()
                                if text and text.strip():
                                    contextRows.append({
                                        "page_number": pageNum,
                                        "row_order": len(contextRows) + 1,
                                        "context_type": "narrative",
                                        "content": text.strip(),
                                        "table_ref": None
                                    })
                            except Exception:
                                pass

                        pageProducedContent = (
                            len(contextRows) > contextRowsBefore
                            or len(pendingTables) > pendingTablesBefore
                        )
                        if not pageProducedContent:
                            logger.info(
                                f"Page {pageNum}: native extraction empty – "
                                f"attempting Groq vision OCR."
                            )
                            if ocrExtractor is None:
                                ocrExtractor = PdfOcrExtractor()

                            fitzPage = fitzDoc.load_page(pageNum - 1)
                            b64Img = PdfOcrExtractor.renderPageToBase64(
                                fitzPage, dpi=ocrExtractor.cfg.dpi
                            )
                            ocrResult = ocrExtractor.extractPage(b64Img)

                            ocrNarrative = (ocrResult.get("narrative") or "").strip()
                            if ocrNarrative:
                                contextRows.append({
                                    "page_number": pageNum,
                                    "row_order": len(contextRows) + 1,
                                    "context_type": "narrative",
                                    "content": ocrNarrative,
                                    "table_ref": None
                                })

                            for ocrTable in ocrResult.get("tables", []):
                                if not ocrTable or len(ocrTable) < 2:
                                    continue
                                ocrHeaders = [
                                    str(h).strip() if h and str(h).strip() else f"column_{i}"
                                    for i, h in enumerate(ocrTable[0])
                                ]
                                seen = {}
                                uniqueOcrHeaders = []
                                for h in ocrHeaders:
                                    if h in seen:
                                        seen[h] += 1
                                        uniqueOcrHeaders.append(f"{h}_{seen[h]}")
                                    else:
                                        seen[h] = 0
                                        uniqueOcrHeaders.append(h)

                                ocrSignature = "|".join(
                                    col.lower().strip() for col in uniqueOcrHeaders
                                )

                                ocrDataRows = [
                                    row for row in ocrTable[1:]
                                    if isinstance(row, list)
                                    and [str(c).strip().lower() if c else "" for c in row]
                                    != [col.lower().strip() for col in uniqueOcrHeaders]
                                ]
                                numCols = len(uniqueOcrHeaders)
                                ocrDataRows = [
                                    (row + [""] * numCols)[:numCols]
                                    for row in ocrDataRows
                                ]
                                ocrDf = pd.DataFrame(ocrDataRows, columns=uniqueOcrHeaders)
                                ocrDf.dropna(how="all", inplace=True)
                                ocrDf.reset_index(drop=True, inplace=True)
                                if ocrDf.empty:
                                    continue

                                ocrMerged = False
                                ocrPendingIdx = None
                                for i, pending in enumerate(pendingTables):
                                    if (
                                        pending["signature"] == ocrSignature
                                        and pending["lastPage"] == pageNum - 1
                                    ):
                                        pending["df"] = pd.concat(
                                            [pending["df"], ocrDf], ignore_index=True
                                        )
                                        pending["lastPage"] = pageNum
                                        ocrMerged = True
                                        ocrPendingIdx = i
                                        break

                                if not ocrMerged:
                                    pendingTables.append({
                                        "signature": ocrSignature,
                                        "headers": uniqueOcrHeaders,
                                        "df": ocrDf,
                                        "lastPage": pageNum,
                                    })
                                    ocrPendingIdx = len(pendingTables) - 1

                                ocrCtxType = (
                                    "table_continuation" if ocrMerged else "table_anchor"
                                )
                                ocrAnchor = (
                                    f"[Table continued: {', '.join(uniqueOcrHeaders)}]"
                                    if ocrMerged
                                    else f"[Table: {', '.join(uniqueOcrHeaders)}]"
                                )
                                contextRows.append({
                                    "page_number": pageNum,
                                    "row_order": len(contextRows) + 1,
                                    "context_type": ocrCtxType,
                                    "content": ocrAnchor,
                                    "table_ref": ocrPendingIdx,
                                })

                for row in contextRows:
                    if row["table_ref"] is not None:
                        row["table_ref"] = f"{baseName}_table{row['table_ref'] + 1}"

                if not pendingTables and not contextRows:
                    raise CustomException(
                        ValueError("No extractable content in PDF"),
                        statusCode=422,
                        uiMessage="No extractable content found in the PDF. Ensure the PDF contains selectable text or tables."
                    )

                for idx, entry in enumerate(pendingTables, start=1):
                    with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                        entry["df"].to_parquet(temp.name, compression="snappy")
                        fileName = f"{baseName}_table{idx}.parquet"
                        self.client.storage.from_("AnalyticsHub").upload(
                            file=temp.name,
                            path=f"{projectId}/{fileName}",
                            file_options={"upsert": "true"}
                        )

                if contextRows:
                    textDf = pd.DataFrame(contextRows)
                    with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                        textDf.to_parquet(temp.name, compression="snappy")
                        fileName = f"{baseName}_text.parquet"
                        self.client.storage.from_("AnalyticsHub").upload(
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
                self.client.storage.from_("AnalyticsHub").upload(
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
                self.client.storage.from_("AnalyticsHub").upload(
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
                self.client.storage.from_("AnalyticsHub").upload(
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
            _ = self.client.storage.from_("AnalyticsHub").remove(f"{tableDetails.projectId}/{tableDetails.tableName}" + ".parquet")
            fileUrl = os.environ["FILE_URL"].format(projectId = tableDetails.projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            jsonData = json.loads(urlopen(fileUrl).read())
            jsonData.pop(tableDetails.tableName)
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(jsonData, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(path = f"{tableDetails.projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            updateProjectModifiedAt(tableDetails.projectId)
            return
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception

dataLoadService = DataLoadService()
