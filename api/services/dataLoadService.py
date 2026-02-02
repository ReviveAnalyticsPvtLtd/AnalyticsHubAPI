"""
dataLoadService.py

dataLoadService module provides services for loading and managing data from various sources (CSV, Excel, MySQL, PostgreSQL, MongoDB) and deleting tables for AnalyticsHub projects.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["dataLoadService"] 


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
import time
import io
import os

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
                    self.client.storage.from_("AnalyticsHub").upload(
                        file=temp.name,
                        path=f"{projectId}/{os.path.splitext(file.filename)[0]}.parquet",
                        file_options={"upsert": "true"}
                    )
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
                for sheetName, sheetData in allSheetData.items():
                    with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                        sheetData.to_parquet(temp.name, compression="snappy")
                        fileName = f"{os.path.splitext(file.filename)[0]}_{sheetName}.parquet"
                        self.client.storage.from_("AnalyticsHub").upload(
                            file=temp.name,
                            path=f"{projectId}/{fileName}",
                            file_options={"upsert": "true"}
                        )
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
            with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                pd.read_sql(f"SELECT * FROM {connection.table}", engine).to_parquet(
                    temp.name, compression="snappy"
                )
                self.client.storage.from_("AnalyticsHub").upload(
                    file=temp.name,
                    path=f"{connection.projectId}/{connection.table}.parquet",
                    file_options={"upsert": "true"}
                )
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
            with tempfile.NamedTemporaryFile(delete=True, suffix=".parquet") as temp:
                pd.read_sql(f"SELECT * FROM {connection.table}", engine).to_parquet(
                    temp.name, compression="snappy"
                )
                self.client.storage.from_("AnalyticsHub").upload(
                    file=temp.name,
                    path=f"{connection.projectId}/{connection.table}.parquet",
                    file_options={"upsert": "true"}
                )
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

                self.client.storage.from_("AnalyticsHub").upload(
                    file=temp.name,
                    path=f"{connection.projectId}/{connection.collection}.parquet",
                    file_options={"upsert": "true"}
                )
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
            return
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception

dataLoadService = DataLoadService()
