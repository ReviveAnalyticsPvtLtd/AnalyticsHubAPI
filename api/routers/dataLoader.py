"""
API router for data loading operations.

This module provides endpoints for loading data from various sources (CSV, Excel, PDF, MySQL, PostgreSQL, MongoDB) and deleting tables.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.models import (
    LoadMySQLorPostgreSQL,
    LoadMongoDB,
    DeleteTable
)
from utils.exceptionHandler import CustomException, raiseHttpException
from fastapi import APIRouter, Depends, UploadFile, File, Form
from api.services.dataLoadService import dataLoadService
from fastapi.responses import ORJSONResponse
from api.commons import verifyToken, requireCredits, UserContext
from typing import Annotated

router = APIRouter()
"""
Router for data loading-related endpoints.
"""

@router.post("/loadCsvData")
async def loadCsvData(projectId: Annotated[str, Form()], files: list[UploadFile], user: UserContext = Depends(requireCredits("metadata_generation"))):
    """
    Load data from CSV file(s) into the specified project and generate metadata
    for the newly added tables.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The CSV files to upload.
        user: UserContext injected after credit validation.

    Returns:
        ORJSONResponse: Success with generated metadata for the new tables.
    """
    try:
        metadata = await dataLoadService.loadCsvData(projectId=projectId, files=files, userId=user.userId)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS", "metadata": metadata})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/loadExcelData")
async def loadExcelData(projectId: Annotated[str, Form()], files: list[UploadFile], user: UserContext = Depends(requireCredits("metadata_generation"))):
    """
    Load data from Excel file(s) into the specified project and generate metadata
    for the newly added tables.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The Excel files to upload.
        user: UserContext injected after credit validation.

    Returns:
        ORJSONResponse: Success with generated metadata for the new tables.
    """
    try:
        metadata = await dataLoadService.loadExcelData(projectId=projectId, files=files, userId=user.userId)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS", "metadata": metadata})
    except CustomException as e:
        raiseHttpException(e)

@router.post("/loadPdfData")
async def loadPdfData(projectId: Annotated[str, Form()], files: list[UploadFile], user: UserContext = Depends(requireCredits("pdf_extraction_per_page"))):
    """
    Load data from PDF files into the specified project and generate metadata
    for the extracted tables.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The PDF files to upload.
        user: UserContext injected after credit validation.

    Returns:
        ORJSONResponse: Success with generated metadata for the new tables.
    """
    try:
        metadata = await dataLoadService.loadPdfData(projectId=projectId, files=files, userId=user.userId)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS", "metadata": metadata})
    except CustomException as e:
        raiseHttpException(e)

@router.post("/loadMySql")
async def loadMySql(connection: LoadMySQLorPostgreSQL, user: UserContext = Depends(requireCredits("metadata_generation"))):
    """
    Load data from a MySQL database connection and generate metadata for the
    loaded table.

    Args:
        connection (LoadMySQLorPostgreSQL): Connection details for MySQL.
        user: UserContext injected after credit validation.

    Returns:
        ORJSONResponse: Success with generated metadata for the new table.
    """
    try:
        metadata = dataLoadService.loadMySql(connection=connection, userId=user.userId)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS", "metadata": metadata})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/loadPostgreSQL")
async def loadPostgreSQL(connection: LoadMySQLorPostgreSQL, user: UserContext = Depends(requireCredits("metadata_generation"))):
    """
    Load data from a PostgreSQL database connection and generate metadata for the
    loaded table.

    Args:
        connection (LoadMySQLorPostgreSQL): Connection details for PostgreSQL.
        user: UserContext injected after credit validation.

    Returns:
        ORJSONResponse: Success with generated metadata for the new table.
    """
    try:
        metadata = dataLoadService.loadPostgreSQL(connection=connection, userId=user.userId)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS", "metadata": metadata})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/loadMongoDB")
async def loadMongoDB(connection: LoadMongoDB, user: UserContext = Depends(requireCredits("metadata_generation"))):
    """
    Load data from a MongoDB database connection and generate metadata for the
    loaded collection.

    Args:
        connection (LoadMongoDB): Connection details for MongoDB.
        user: UserContext injected after credit validation.

    Returns:
        ORJSONResponse: Success with generated metadata for the new table.
    """
    try:
        metadata = dataLoadService.loadMongoDB(connection=connection, userId=user.userId)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS", "metadata": metadata})
    except CustomException as e:
        raiseHttpException(e)

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
    except CustomException as e:
        raiseHttpException(e)