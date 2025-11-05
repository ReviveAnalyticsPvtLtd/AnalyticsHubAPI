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
        Loads CSV data into the project, converts it to Parquet format, and uploads it to storage.

        Args:
            projectId (str): The project identifier.
            files (list): The list of uploaded CSV files.
        Returns:
            None
        Raises:
            CustomException: If loading or uploading fails.
        """
        try:
            for file in files:              
                with tempfile.NamedTemporaryFile(delete = True, suffix = ".parquet") as temp:
                    pd.read_csv(io.BytesIO(await file.read()), parse_dates = True).to_parquet(temp.name, compression = "snappy")
                    _ = self.client.storage.from_("AnalyticsHub").upload(
                        file = temp.name,
                        path = f"{projectId}/{os.path.splitext(file.filename)[0] + '.parquet'}",
                        file_options = {"upsert": "true"}
                    )
                    temp.close()
            return 
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
    async def loadExcelData(self, projectId: Annotated[str, Form()], files: list[UploadFile]) -> None:
        """
        Loads Excel data (optionally from a specific sheet) into the project, converts it to Parquet format, and uploads it to storage.

        Args:
            projectId (str): The project identifier.
            files (list): The list of uploaded excels files.
        Returns:
            None
        Raises:
            CustomException: If loading or uploading fails.
        """
        try:
            for file in files:
                allSheetData = pd.read_excel(io.BytesIO(await file.read()), sheet_name = None, parse_dates = True)
                for sheetName, sheetData in allSheetData.items():
                    with tempfile.NamedTemporaryFile(delete = True, suffix = ".parquet") as temp:
                        sheetData.to_parquet(temp.name, compression = "snappy")
                        fileName = f"{os.path.splitext(file.filename)[0] + '_' + sheetName + '.parquet'}"
                        _ = self.client.storage.from_("AnalyticsHub").upload(
                            file = temp.name,
                            path = f"{projectId}/{fileName}",
                            file_options = {"upsert": "true"}
                        )
                        temp.close()
            return
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
    def loadMySql(connection: LoadMySQLorPostgreSQL) -> None:
        """
        Loads data from a MySQL database table into the project, converts it to Parquet format, and uploads it to storage.

        Args:
            connection (LoadMySQLorPostgreSQL): Connection details for the MySQL database.
        Returns:
            None
        Raises:
            CustomException: If loading or uploading fails.
        """
        try:
            with tempfile.NamedTemporaryFile(delete = True, suffix = ".parquet") as temp:
                connStr = f"mysql+pymysql://{connection.user}:{connection.password}@{connection.host}:{connection.port}/{connection.db}"
                engine = create_engine(connStr)
                pd.read_sql(f"SELECT * FROM {connection.table}", engine, parse_dates = True).to_parquet(temp.name, compression = "snappy")
                _ = client.storage.from_("AnalyticsHub").upload(
                    file = temp.name,
                    path = f"{connection.projectId}/{connection.table + '.parquet'}",
                    file_options = {"upsert": "true"}
                )
                if "connections.json" in [x.get("name") for x in client.storage.from_("AnalyticsHub").list(path = connection.projectId)]:
                    databaseConnectionsUrl = os.environ["FILE_URL"].format(projectId = connection.projectId, fileName = "connections.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                    databaseConnections = json.loads(urlopen(databaseConnectionsUrl).read())
                    connectionDetails = connection.model_dump()
                    connectionDetails.pop("projectId")
                    connectionDetails["type"] = "MySQL/PostgreSQL"
                    databaseConnections[max([int(x) for x in databaseConnections.keys()]) + 1] = connectionDetails
                else:
                    connectionDetails = connection.model_dump()
                    connectionDetails.pop("projectId")
                    connectionDetails["type"] = "MySQL/PostgreSQL"
                    databaseConnections = {"1": connectionDetails}
                with io.BytesIO() as buffer:
                    buffer.write(json.dumps(databaseConnections, indent=4).encode("utf-8"))
                    buffer.seek(0)
                    _ = client.storage.from_("AnalyticsHub").upload(path = f"{connection.projectId}/connections.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
                temp.close()
            return
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
            
    def loadPostgreSQL(self, connection: LoadMySQLorPostgreSQL) -> None:
        """
        Loads data from a PostgreSQL database table into the project, converts it to Parquet format, and uploads it to storage.

        Args:
            connection (LoadMySQLorPostgreSQL): Connection details for the PostgreSQL database.
        Returns:
            None
        Raises:
            CustomException: If loading or uploading fails.
        """
        try:
            with tempfile.NamedTemporaryFile(delete = True, suffix = ".parquet") as temp:
                connStr = f"postgresql+psycopg2://{connection.user}:{connection.password}@{connection.host}:{connection.port}/{connection.db}"
                engine = create_engine(connStr)
                pd.read_sql(f"SELECT * FROM {connection.table}", engine, parse_dates = True).to_parquet(temp.name, compression = "snappy")
                _ = self.client.storage.from_("AnalyticsHub").upload(
                    file = temp.name,
                    path = f"{connection.projectId}/{connection.table + '.parquet'}",
                    file_options = {"upsert": "true"}
                )
                temp.close()
            return
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
    def loadMongoDB(self, connection: LoadMongoDB) -> None:
        """
        Loads data from a MongoDB collection into the project, converts it to Parquet format, and uploads it to storage.

        Args:
            connection (LoadMongoDB): Connection details for the MongoDB database.
        Returns:
            None
        Raises:
            CustomException: If loading or uploading fails.
        """
        try:
            with tempfile.NamedTemporaryFile(delete = True, suffix = ".parquet") as temp:
                mongoClient = MongoClient(connection.connectionString, server_api=ServerApi('1'))
                records = list(mongoClient[connection.db][connection.collection].find())
                for record in records: record.pop("_id")
                pd.DataFrame(records).to_parquet(temp.name, compression = "snappy")
                _ = self.client.storage.from_("AnalyticsHub").upload(
                    file = temp.name,
                    path = f"{connection.projectId}/{connection.collection + '.parquet'}",
                    file_options = {"upsert": "true"}
                )
                temp.close()
            return
        except Exception as e:
            exception  = CustomException(e)
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
