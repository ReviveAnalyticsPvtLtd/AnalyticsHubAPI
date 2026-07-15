"""
API router for data loading operations.

This module provides endpoints for loading data from various sources (CSV, Excel, PDF, MySQL, PostgreSQL, MongoDB) and deleting tables.
"""

__version__ = "1.0.1"
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
from api.commons import verifyMultipartProjectOwnership, verifyProjectOwnershipDirect, verifyUser, UserContext, requireCredits
import asyncio
from typing import Annotated

router = APIRouter()
"""
Router for data loading-related endpoints.
"""

@router.post("/loadCsvData")
async def loadCsvData(
    projectId: Annotated[str, Form()],
    files: list[UploadFile],
    userId: str = Depends(verifyMultipartProjectOwnership)
):
    """
    Load data from a CSV file into the specified project.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The CSV files to upload.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await dataLoadService.loadCsvData(projectId=projectId, files=files)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS"})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/loadExcelData")
async def loadExcelData(
    projectId: Annotated[str, Form()],
    files: list[UploadFile],
    userId: str = Depends(verifyMultipartProjectOwnership)
):
    """
    Load data from an Excel file into the specified project.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The Excel files to upload.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await dataLoadService.loadExcelData(projectId=projectId, files=files)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS"})
    except CustomException as e:
        raiseHttpException(e)

@router.post("/loadPdfData")
async def loadPdfData(
    projectId: Annotated[str, Form()],
    files: list[UploadFile],
    user: UserContext = Depends(requireCredits("pdf_extraction_per_page")),
    userId: str = Depends(verifyMultipartProjectOwnership)
):
    """
    Load data from PDF files into the specified project.
    Extracts tables and text content from the PDF.

    Args:
        projectId (str): The ID of the project to load data into.
        files (list[UploadFile]): The PDF files to upload.
        user: UserContext injected after credit validation.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await dataLoadService.loadPdfData(projectId=projectId, files=files, userId=user.userId)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS"})
    except CustomException as e:
        raiseHttpException(e)

@router.post("/loadMySql")
async def loadMySql(connection: LoadMySQLorPostgreSQL, user: UserContext = Depends(verifyUser)):
    """
    Load data from a MySQL database connection.

    Args:
        connection (LoadMySQLorPostgreSQL): Connection details for MySQL.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(connection.projectId, user.userId)
        await asyncio.to_thread(dataLoadService.loadMySql, connection=connection)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS"})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/loadPostgreSQL")
async def loadPostgreSQL(connection: LoadMySQLorPostgreSQL, user: UserContext = Depends(verifyUser)):
    """
    Load data from a PostgreSQL database connection.

    Args:
        connection (LoadMySQLorPostgreSQL): Connection details for PostgreSQL.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(connection.projectId, user.userId)
        await asyncio.to_thread(dataLoadService.loadPostgreSQL, connection=connection)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS"})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/loadMongoDB")
async def loadMongoDB(connection: LoadMongoDB, user: UserContext = Depends(verifyUser)):
    """
    Load data from a MongoDB database connection.

    Args:
        connection (LoadMongoDB): Connection details for MongoDB.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(connection.projectId, user.userId)
        await asyncio.to_thread(dataLoadService.loadMongoDB, connection=connection)
        return ORJSONResponse(status_code=200, content={"status": "SUCCESS"})
    except CustomException as e:
        raiseHttpException(e)

@router.delete("/deleteTable")
async def deleteTable(tableDetails: DeleteTable, user: UserContext = Depends(verifyUser)):
    """
    Delete a table from the data source.

    Args:
        tableDetails (DeleteTable): Details of the table to delete.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(tableDetails.projectId, user.userId)
        await asyncio.to_thread(dataLoadService.deleteTable, tableDetails = tableDetails)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Table deleted successfully"})
    except CustomException as e:
        raiseHttpException(e)
