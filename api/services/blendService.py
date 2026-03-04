"""
blendService.py

This module provides the BlendService class, which encapsulates business logic for creating data blends, retrieving data sources, and extracting fields from sources for AnalyticsHub projects. It interacts with the Supabase client and manages blend configurations and metadata in storage.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["blendService"] 


from api.commons import updateProjectModifiedAt
from utils.exceptionHandler import CustomException
from urllib.request import urlopen
from utils.logger import logger
from api.commons import client
from api.models import (
    CreateDataBlend,
    GetFieldsFromSources
)
import time
import json
import os
import io

class BlendService:
    """
    Service class for managing data blending operations.

    Handles creation of data blends, retrieval of data sources, and extraction of fields from sources.
    Interacts with the Supabase client and manages blend configurations and metadata in storage.
    """
    def __init__(self) -> None:
        """
        Initializes the BlendService and sets up the Supabase client.
        """
        logger.info("Initializing Blend Service.")
        self.client = client

    def createDataBlend(self, blendDetails: CreateDataBlend) -> None:
        """
        Create a new data blend configuration for a project.

        Args:
            blendDetails (CreateDataBlend): Details of the blend to create, including tables, join configuration, and blend name.

        Raises:
            CustomException: For any errors during blend creation.
        """
        try:
            joinConfig = {
                "tables": blendDetails.tables,
                "blendOn": blendDetails.blendOn,
                "joinTypes": blendDetails.joinTypes
            }
            if "blendConfig.json" in [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = blendDetails.projectId)]:
                fileUrl = os.environ["FILE_URL"].format(projectId = blendDetails.projectId, fileName = "blendConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                blendConfig = json.loads(urlopen(fileUrl).read())
                blendConfig[blendDetails.blendName] = joinConfig
            else:
                blendConfig = {blendDetails.blendName: joinConfig}
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(blendConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = self.client.storage.from_("AnalyticsHub").upload(path = f"{blendDetails.projectId}/blendConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            updateProjectModifiedAt(blendDetails.projectId)
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def getDataSources(self, projectId: str) -> dict:
        """
        Retrieve all data sources (raw tables and blends) for a given project.

        Args:
            projectId (str): The project identifier.

        Returns:
            dict: Dictionary containing blends, raw tables, and blended tables.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            if "blendConfig.json" in [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = projectId)]:
                blendConfigUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "blendConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                blendConfig = json.loads(urlopen(blendConfigUrl).read())
                blendedTables = list(blendConfig.keys())
                blends = [
                    {"blendName": x, "tables": blendConfig[x].get("tables"), "joinTypes": blendConfig[x].get("joinTypes"), "blendOn": blendConfig[x].get("blendOn")} for x in blendedTables
                ]
            else:
                blends, blendedTables = list(), list()
            metadataUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            metadata = json.loads(urlopen(metadataUrl).read())
            rawTables = list(metadata.keys())
            dataSources = {
                "blends": blends,
                "rawTables": rawTables,
                "blendedTables": blendedTables
            }
            return dataSources
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def getFieldsFromSources(self, details: GetFieldsFromSources) -> dict:
        """
        Extract and categorize fields (numerical, categorical, datetime) from a given data source (raw or blend).

        Args:
            details (GetFieldsFromSources): Details specifying the project and table/source name.

        Returns:
            dict: Dictionary with lists of numerical, categorical, and datetime columns.

        Raises:
            CustomException: For any errors during extraction.
        """
        try:
            allFiles = [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = details.projectId)]
            metadataUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            metadata = json.loads(urlopen(metadataUrl).read())
            if details.tableName in metadata.keys():
                allFields = metadata[details.tableName]["columns"]
            elif "blendConfig.json" in allFiles:
                blendConfigUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "blendConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                blendConfig = json.loads(urlopen(blendConfigUrl).read())
                allFields = list()
                tablesUsed = blendConfig[details.tableName].get("tables")
                for table in tablesUsed:
                    allFields.extend(metadata[table]["columns"])
            else:
                pass
            numericals = ["int64", "float64", "float32", "int32"]
            categoricals = ["bool", "category", "object", "string"]
            datetimeTypes = ["datetime64[ns]", "datetime64[ns, tz]"]
            numericalColumns, categoricalColumns, datetimeColumns = list(), list(), list()
            for column in allFields:
                if column.get("type") in categoricals:
                    categoricalColumns.append(column["name"])
                elif column["type"] in numericals:
                    numericalColumns.append(column["name"])
                elif column["type"] in datetimeTypes:
                    datetimeColumns.append(column["name"])
            response = {
                "numericalColumns": numericalColumns,
                "categoricalColumns": categoricalColumns,
                "datetimeColumns": datetimeColumns
            } 
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
blendService = BlendService()