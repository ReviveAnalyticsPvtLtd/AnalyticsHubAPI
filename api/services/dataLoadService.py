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

    def _columnsMatchFuzzy(self, sigA: str, sigB: str) -> bool:
        """
        Check if two table signatures match fuzzily.
        Signatures are composed of lowercased column names separated by '|'.
        Allows for minor variations in column names across pages.
        """
        colsA = [re.sub(r'[^a-z0-9]', '', c) for c in sigA.split('|')]
        colsB = [re.sub(r'[^a-z0-9]', '', c) for c in sigB.split('|')]
        
        if not colsA or not colsB:
            return False
            
        if len(colsA) != len(colsB):
            return False
            
        matches = sum(1 for a, b in zip(colsA, colsB) if a == b)
        matchRatio = matches / len(colsA)
        
        return matchRatio >= 0.8

    def _looksLikeHeaders(self, row: list) -> bool:
        """
        Heuristic: does this row look like column headers rather than data?
        Returns True only if the row contains strong signals like snake_case
        or column_N, and contains NO numeric values.
        """
        if not row:
            return False
            
        strongHeaderCount = 0
        numericCount = 0
        
        for cell in row:
            text = str(cell).strip() if cell else ""
            if not text:
                continue
                
            # Numeric values are data, not headers
            try:
                float(text.replace(",", ""))
                numericCount += 1
                continue
            except ValueError:
                pass
                
            # Strong signal: snake_case pattern (e.g. stock_symbol, stock_name)
            if re.match(r'^[a-z][a-z0-9]*(_[a-z0-9]+)+$', text):
                strongHeaderCount += 1
            # Strong signal: column_N placeholder
            elif re.match(r'^column_\d+$', text, re.IGNORECASE):
                strongHeaderCount += 1
                
        # If any numeric values exist, it's almost certainly data
        if numericCount > 0:
            return False
            
        # Consider it headers if we have strong signals for at least half the cells
        return strongHeaderCount > 0 and strongHeaderCount >= len(row) // 2

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
        Load PDF files into project storage. Extracts tables from PDFs.
        Tables spanning multiple pages are automatically merged when their header
        signatures match across consecutive pages, or when a table starts near
        the top of a page and a pending table exists from the previous page.
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
                pendingTables = []

                ocrExtractor = None

                totalPages = 0
                with fitz.open(stream=fileBytes, filetype="pdf") as fitzDoc, \
                     pdfplumber.open(io.BytesIO(fileBytes)) as pdf:
                    totalPages = len(pdf.pages)
                    logger.info(f"Processing {file.filename}: {totalPages} pages")
                    for pageNum, page in enumerate(pdf.pages, start=1):
                        pendingTablesBefore = len(pendingTables)
                        pageProducedContent = False

                        detectedTables = page.find_tables()
                        
                        sortedTableEntries = []
                        if detectedTables:
                            pageProducedContent = True
                            sortedTableEntries = sorted(
                                [(t.bbox, t.extract()) for t in detectedTables],
                                key=lambda x: x[0][1]
                            )
                        else:
                            # Fallback for borderless tables using explicit x_tolerance words grouping
                            words = page.extract_words(x_tolerance=5, y_tolerance=3, keep_blank_chars=False)
                            if words:
                                words.sort(key=lambda w: (round(w['top'], 1), w['x0']))
                                lines = {}
                                for w in words:
                                    y = round(w['top'], 1)
                                    if y not in lines:
                                        lines[y] = []
                                    lines[y].append(w['text'])
                                
                                # Convert lines dictionary to table data
                                fallbackTableData = [line for line in lines.values() if len(line) > 1]
                                if len(fallbackTableData) >= 2:
                                    pageProducedContent = True
                                    # bbox estimate (x0, top, x1, bottom)
                                    min_x = min(w['x0'] for w in words)
                                    min_y = min(w['top'] for w in words)
                                    max_x = max(w['x1'] for w in words)
                                    max_y = max(w['bottom'] for w in words)
                                    
                                    sortedTableEntries = [((min_x, min_y, max_x, max_y), fallbackTableData)]

                        pageHeight = float(page.height)

                        for bbox, tableData in sortedTableEntries:
                            tableTop = float(bbox[1])

                            if not tableData or len(tableData) < 2:
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

                            merged = False
                            pendingIdx = None
                            isContinuationWithoutHeaders = False
                            isReshapedContinuation = False
                            firstRowIsHeaders = self._looksLikeHeaders(tableData[0])
                            
                            for i, pending in enumerate(pendingTables):
                                if pending["lastPage"] == pageNum - 1:
                                    if self._columnsMatchFuzzy(pending["signature"], signature):
                                        # Signatures match → same table with repeated headers
                                        merged = True
                                        pendingIdx = i
                                        break
                                    elif len(pending["headers"]) == len(uniqueHeaders) and not firstRowIsHeaders:
                                        # Same col count but first row is data → continuation without headers
                                        merged = True
                                        pendingIdx = i
                                        isContinuationWithoutHeaders = True
                                        break

                            # Position-based continuation: only if the first row
                            # does NOT look like headers (i.e. it's data, not a
                            # new table starting) and the table starts near the top
                            if not merged and tableTop < pageHeight * 0.15 and not firstRowIsHeaders:
                                for i, pending in enumerate(pendingTables):
                                    if pending["lastPage"] == pageNum - 1:
                                        merged = True
                                        pendingIdx = i
                                        isReshapedContinuation = True
                                        break

                            if isReshapedContinuation:
                                # All rows are data (first row is data, not headers)
                                rawDataRows = tableData
                                effectiveHeaders = pendingTables[pendingIdx]["headers"]
                            elif isContinuationWithoutHeaders:
                                rawDataRows = tableData
                                effectiveHeaders = pendingTables[pendingIdx]["headers"]
                            elif merged:
                                rawDataRows = tableData[1:]
                                effectiveHeaders = pendingTables[pendingIdx]["headers"]
                            else:
                                rawDataRows = tableData[1:]
                                effectiveHeaders = uniqueHeaders

                            # Reshape rows to match expected column count
                            numCols = len(effectiveHeaders)
                            reshapedRows = []
                            for row in rawDataRows:
                                if not isinstance(row, list):
                                    continue
                                # Filter out repeated header rows
                                normalized = [str(c).strip().lower() if c else "" for c in row]
                                if normalized == [col.lower().strip() for col in effectiveHeaders]:
                                    continue
                                if len(row) == numCols:
                                    reshapedRows.append(row)
                                elif len(row) > numCols:
                                    # Join excess cells into the last column
                                    newRow = list(row[:numCols - 1])
                                    newRow.append(" ".join(str(c) for c in row[numCols - 1:] if c))
                                    reshapedRows.append(newRow)
                                else:
                                    # Pad with empty strings
                                    reshapedRows.append((row + [""] * numCols)[:numCols])
                            
                            df = pd.DataFrame(reshapedRows, columns=effectiveHeaders)
                            df.dropna(how="all", inplace=True)
                            df.reset_index(drop=True, inplace=True)
                            if df.empty:
                                continue

                            if merged:
                                mergeType = "reshaped" if isReshapedContinuation else ("no-header" if isContinuationWithoutHeaders else "signature")
                                pending = pendingTables[pendingIdx]
                                pending["df"] = pd.concat(
                                    [pending["df"], df], ignore_index=True
                                )
                                pending["lastPage"] = pageNum
                                logger.info(
                                    f"Page {pageNum}/{totalPages}: merged into table {pendingIdx+1} "
                                    f"({mergeType}, {len(df)} rows)"
                                )
                            else:
                                pendingTables.append({
                                    "signature": signature,
                                    "headers": effectiveHeaders,
                                    "df": df,
                                    "lastPage": pageNum
                                })
                                logger.info(
                                    f"Page {pageNum}/{totalPages}: new table {len(pendingTables)} "
                                    f"({len(effectiveHeaders)} cols: {effectiveHeaders}, {len(df)} rows)"
                                )

                        # OCR fallback if no tables were extracted on this page
                        if not pageProducedContent:
                            try:
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

                                    ocrMerged = False
                                    ocrPendingIdx = None
                                    ocrIsContinuationWithoutHeaders = False
                                    ocrIsReshapedContinuation = False
                                    ocrFirstRowIsHeaders = self._looksLikeHeaders(ocrTable[0])
                                    
                                    for i, pending in enumerate(pendingTables):
                                        if pending["lastPage"] == pageNum - 1:
                                            if self._columnsMatchFuzzy(pending["signature"], ocrSignature):
                                                ocrMerged = True
                                                ocrPendingIdx = i
                                                break
                                            elif len(pending["headers"]) == len(uniqueOcrHeaders) and not ocrFirstRowIsHeaders:
                                                ocrMerged = True
                                                ocrPendingIdx = i
                                                ocrIsContinuationWithoutHeaders = True
                                                break

                                    # Position-based continuation for OCR tables
                                    if not ocrMerged and not ocrFirstRowIsHeaders:
                                        for i, pending in enumerate(pendingTables):
                                            if pending["lastPage"] == pageNum - 1:
                                                ocrMerged = True
                                                ocrPendingIdx = i
                                                ocrIsReshapedContinuation = True
                                                break

                                    if ocrIsReshapedContinuation:
                                        ocrRawDataRows = ocrTable
                                        ocrEffectiveHeaders = pendingTables[ocrPendingIdx]["headers"]
                                    elif ocrIsContinuationWithoutHeaders:
                                        ocrRawDataRows = ocrTable
                                        ocrEffectiveHeaders = pendingTables[ocrPendingIdx]["headers"]
                                    elif ocrMerged:
                                        ocrRawDataRows = ocrTable[1:]
                                        ocrEffectiveHeaders = pendingTables[ocrPendingIdx]["headers"]
                                    else:
                                        ocrRawDataRows = ocrTable[1:]
                                        ocrEffectiveHeaders = uniqueOcrHeaders

                                    # Reshape OCR rows to match expected column count
                                    numCols = len(ocrEffectiveHeaders)
                                    ocrReshapedRows = []
                                    for row in ocrRawDataRows:
                                        if not isinstance(row, list):
                                            continue
                                        normalized = [str(c).strip().lower() if c else "" for c in row]
                                        if normalized == [col.lower().strip() for col in ocrEffectiveHeaders]:
                                            continue
                                        if len(row) == numCols:
                                            ocrReshapedRows.append(row)
                                        elif len(row) > numCols:
                                            newRow = list(row[:numCols - 1])
                                            newRow.append(" ".join(str(c) for c in row[numCols - 1:] if c))
                                            ocrReshapedRows.append(newRow)
                                        else:
                                            ocrReshapedRows.append((row + [""] * numCols)[:numCols])

                                    ocrDf = pd.DataFrame(ocrReshapedRows, columns=ocrEffectiveHeaders)
                                    ocrDf.dropna(how="all", inplace=True)
                                    ocrDf.reset_index(drop=True, inplace=True)
                                    if ocrDf.empty:
                                        continue

                                    if ocrMerged:
                                        pending = pendingTables[ocrPendingIdx]
                                        pending["df"] = pd.concat(
                                            [pending["df"], ocrDf], ignore_index=True
                                        )
                                        pending["lastPage"] = pageNum
                                    else:
                                        pendingTables.append({
                                            "signature": ocrSignature,
                                            "headers": ocrEffectiveHeaders,
                                            "df": ocrDf,
                                            "lastPage": pageNum,
                                        })
                            except Exception as ocrErr:
                                logger.warning(
                                    f"Page {pageNum}: OCR fallback failed – {ocrErr}"
                                )

                if not pendingTables:
                    raise CustomException(
                        ValueError("No extractable tables in PDF"),
                        statusCode=422,
                        uiMessage="No extractable tables found in the PDF. Ensure the PDF contains tables."
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
