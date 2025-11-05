"""
API router for data loading operations.

This module provides endpoints for loading data from various sources (CSV, Excel, MySQL, PostgreSQL, MongoDB) and deleting tables.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.models import (
    LoadMySQLorPostgreSQL,
    LoadMongoDB,
    DeleteTable
)
from fastapi import APIRouter, Depends, UploadFile, File, Form
from api.services.dataLoadService import dataLoadService
from fastapi.exceptions import HTTPException
from fastapi.responses import ORJSONResponse
from api.commons import verifyToken
from typing import Annotated

router = APIRouter()
"""
Router for data loading-related endpoints.
"""

@router.post("/loadCsvData")
async def loadCsvData(projectId: Annotated[str, Form()], files: list[UploadFile], token = Depends(verifyToken)):
    """
    Load data from a CSV file into the specified project.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The CSV files to upload.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await dataLoadService.loadCsvData(projectId = projectId, files = files)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Data loaded successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/loadExcelData")
async def loadExcelData(projectId: Annotated[str, Form()], files: list[UploadFile], token = Depends(verifyToken)):
    """
    Load data from an Excel file into the specified project.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The Excel files to upload.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await dataLoadService.loadExcelData(projectId = projectId, files = files)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Data loaded successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))

@router.post("/loadMySql")
async def loadMySql(connection: LoadMySQLorPostgreSQL, token = Depends(verifyToken)):
    """
    Load data from a MySQL database connection.

    Args:
        connection (LoadMySQLorPostgreSQL): Connection details for MySQL.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        dataLoadService.loadMySql(connection = connection)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Data loaded successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/loadPostgreSQL")
async def loadPostgreSQL(connection: LoadMySQLorPostgreSQL, token = Depends(verifyToken)):
    """
    Load data from a PostgreSQL database connection.

    Args:
        connection (LoadMySQLorPostgreSQL): Connection details for PostgreSQL.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        dataLoadService.loadPostgreSQL(connection = connection)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Data loaded successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/loadMongoDB")
async def loadMongoDB(connection: LoadMongoDB, token = Depends(verifyToken)):
    """
    Load data from a MongoDB database connection.

    Args:
        connection (LoadMongoDB): Connection details for MongoDB.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        dataLoadService.loadMongoDB(connection = connection)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Data loaded successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))

@router.delete("/deleteTable")
async def deleteTable(tableDetails: DeleteTable, token = Depends(verifyToken)):
    """
    Delete a table from the data source.

    Args:
        tableDetails (DeleteTable): Details of the table to delete.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        dataLoadService.deleteTable(tableDetails = tableDetails)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Table deleted successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))