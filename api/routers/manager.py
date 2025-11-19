"""
API router for project and management operations.

This module provides endpoints for project creation, listing, state updates, metadata management, trigger management, and report generation.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.services.managementService import managementService
from fastapi.responses import ORJSONResponse, HTMLResponse
from fastapi.exceptions import HTTPException
from fastapi import APIRouter, Depends
from api.models import (
    UpdateProjectState,
    CreateProject,
    EditMetadata
)
from api.commons import verifyToken

router = APIRouter()
"""
Router for project and management-related endpoints.
"""

@router.post("/createProject")
async def createProject(projectDetails: CreateProject, token = Depends(verifyToken)):
    """
    Create a new project.

    Args:
        projectDetails (CreateProject): The details required to create a new project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success message with the new project ID or error message.
    """
    try:
        projectId = managementService.createProject(projectDetails = projectDetails, token = token)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "projectId": projectId, "message": "Project created successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/createWorkspace/{workspaceName}")
async def createWorkspace(workspaceName: str, token = Depends(verifyToken)):
    """
    Create a new workspace.

    Args:
        workspaceName (str): The name required to create a new workspace.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success message with the new project ID or error message.
    """
    try:
        workspaceId = managementService.createWorkspace(workspaceName = workspaceName, token = token)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "workspaceId": workspaceId, "message": "Workspace created successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.get("/listProjects/{workspaceId}")
async def listProjects(workspaceId: str, token = Depends(verifyToken)):
    """
    List all projects accessible to the user.

    Args:
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of projects or error message.
    """
    try:
        data = managementService.listProjects(workspaceId = workspaceId, token = token)
        return ORJSONResponse(status_code = 200, content = {"projects": data.to_dict(orient = "records")})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.get("/listWorkspaces")
async def listWorkspaces(token = Depends(verifyToken)):
    """
    List all workspaces accessible to the user.

    Args:
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of workspaces or error message.
    """
    try:
        data = managementService.listWokspaces(token = token)
        return ORJSONResponse(status_code = 200, content = {"projects": data.to_dict(orient = "records")})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.patch("/updateBookmark")
async def updateBookmark(updateBookmarkDetails: UpdateProjectState, token = Depends(verifyToken)):
    """
    Update the bookmark status of a project.

    Args:
        updateBookmarkDetails (UpdateProjectState): Details for updating bookmark status.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        managementService.updateBookmark(updateBookmarkDetails = updateBookmarkDetails)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project bookmark status updated successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.patch("/updateArchive")
async def updateArchive(updateArchiveDetails: UpdateProjectState, token = Depends(verifyToken)):
    """
    Update the archive status of a project.

    Args:
        updateArchiveDetails (UpdateProjectState): Details for updating archive status.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        managementService.updateArchive(updateArchiveDetails = updateArchiveDetails)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project archive status updated successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.patch("/updateTrash")
async def updateTrash(updateTrashDetails: UpdateProjectState, token = Depends(verifyToken)):
    """
    Update the trash status of a project.

    Args:
        updateTrashDetails (UpdateProjectState): Details for updating trash status.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
            managementService.updateTrash(updateTrashDetails = updateTrashDetails)
            return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project trash status updated successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/generateMetadata/{projectId}")
async def generateMetadata(projectId: str, token = Depends(verifyToken)):
    """
    Generate metadata for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Generated metadata and insights or error message.
    """
    try:
        jsonData = managementService.generateMetadata(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "metadata": jsonData})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/generateKpis/{projectId}")
async def generateKpis(projectId: str, token = Depends(verifyToken)):
    """
    Generate important KPIs for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Generated metadata and insights or error message.
    """
    try:
        jsonData = managementService.generateInsightsForProject(projectId = projectId)
        response = {"status": "SUCCESS"}
        response.update(jsonData)
        return ORJSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.get("/getInsights/{projectId}")
async def getInsights(projectId: str, token = Depends(verifyToken)):
    """
    Retrieve insights and their status for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Project metadata or error message.
    """
    try:
        newJson = managementService.getInsights(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = newJson) 
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))

@router.get("/getMetadata/{projectId}")
async def getMetadata(projectId: str, token = Depends(verifyToken)):
    """
    Retrieve metadata for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Project metadata or error message.
    """
    try:
        newJson = managementService.getMetadata(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = newJson) 
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))

@router.put("/editMetadata")
async def editMetadata(modifiedMetadata: EditMetadata, token = Depends(verifyToken)):
    """
    Edit the metadata for a project.

    Args:
        modifiedMetadata (EditMetadata): The modified metadata details.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Updated metadata or error message.
    """
    try:
        jsonData = managementService.editMetadata(modifiedMetadata = modifiedMetadata)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "metadata": jsonData})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))

@router.delete("/deleteProject")
async def deleteProject(projectId: str, token = Depends(verifyToken)):
    """
    Delete a project by its ID.

    Args:
        projectId (str): The ID of the project to delete.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        managementService.deleteProject(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project deleted successfully"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.get("/listTriggers/{projectId}")
async def listTriggers(projectId: str, token = Depends(verifyToken)):
    """
    List all triggers for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of triggers or error message.
    """
    try:
        triggers = managementService.listTriggers(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "triggers": triggers})  
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.get("/listTriggersUnderUserId")
async def listTriggers(token = Depends(verifyToken)):
    """
    List all triggers assigned to the current user.

    Args:
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of triggers or error message.
    """
    try:
        allTriggers = managementService.listTriggersUnderUserId(token = token)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "triggersAssignedToUser": allTriggers})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/generateReport/{projectId}")
async def generateReport(projectId: str, token = Depends(verifyToken)):
    """
    Generate a report for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Generated report HTML content or error message.
    """
    try:
        reports = managementService.generateReport(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "reportHtmlContent": reports})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.get("/getReport/{projectId}/{tableName}")
async def getReport(projectId: str, tableName: str, token = Depends(verifyToken)):
    """
    Retrieve a report for a specific table in a project.

    Args:
        projectId (str): The ID of the project.
        tableName (str): The name of the table.
        token: Authorization token dependency.

    Returns:
        HTMLResponse: HTML content of the report or error message.
    """
    try:
        htmlContent = managementService.getReport(projectId = projectId, tableName = tableName)
        return HTMLResponse(status_code = 200, content = htmlContent)  
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")