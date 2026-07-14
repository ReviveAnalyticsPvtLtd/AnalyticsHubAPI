"""
API router for project and management operations.

This module provides endpoints for project creation, listing, state updates, metadata management, trigger management, and report generation.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from utils.exceptionHandler import CustomException, raiseHttpException
import asyncio
from nubrix.triggers.celery import celeryApp
from fastapi import APIRouter, Depends, Form, Request
from api.services.managementService import managementService
from fastapi.responses import ORJSONResponse, HTMLResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from api.models import (
    UpdateProjectState,
    CreateProject,
    EditMetadata,
    RenameProject
)
from api.commons import verifyToken, verifyProjectOwnership, verifyProjectOwnershipDirect, verifyUser, requireCredits, UserContext

router = APIRouter()
"""
Router for project and management-related endpoints.
"""

@router.post("/createProject")
async def createProject(projectDetails: CreateProject, token = Depends(verifyToken)):
    """
    Create a new project.

    Status Codes:
        200 - Success
        401 - Please login to create a project.
        409 - A project with this name already exists in the workspace.
        422 - Invalid project details.
        500 - Failed to create project. Try again later.
    """
    try:
        projectId = await asyncio.to_thread(managementService.createProject, projectDetails=projectDetails, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "projectId": projectId,
                "message": "Project created successfully"
            }
        )
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/createWorkspace/{workspaceName}")
async def createWorkspace(workspaceName: str, token = Depends(verifyToken)):
    """
    Create a new workspace.

    Status Codes:
        200 - Success
        401 - Please login to create a workspace.
        409 - Workspace with this name already exists.
        422 - Invalid workspace name.
        500 - Unable to create workspace. Try again later.
    """
    try:
        workspaceId = await asyncio.to_thread(managementService.createWorkspace, workspaceName=workspaceName, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "workspaceId": workspaceId,
                "message": "Workspace created successfully"
            }
        )
    except CustomException as e:
        raiseHttpException(e)
    
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
    except CustomException as e:
        raiseHttpException(e)
    
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
        response = managementService.listWokspaces(token = token)
        return ORJSONResponse(status_code = 200, content = response)
    except CustomException as e:
        raiseHttpException(e)

@router.patch("/updateCurrentWorkspace/{updatedWorkspaceId}")
async def updateCurrentWorkspace(updatedWorkspaceId: str, token = Depends(verifyToken)):
    """
    Update the workspace id of a user.

    Args:
        updatedWorkspaceId (str): Details for updating current workspace of a user.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        managementService.updateCurrentWorkspace(updatedWorkspaceId = updatedWorkspaceId, token = token)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Current workspace updated successfully"})
    except CustomException as e:
        raiseHttpException(e)

@router.patch("/updateWorkspaceName/{workspaceId}/{newWorkspaceName}")
async def updateWorkspaceName(workspaceId: str, newWorkspaceName: str, token = Depends(verifyToken)):
    """
    Update the name of a workspace.

    Status Codes:
        200 - Success
        401 - Please login to update workspace.
        404 - Workspace not found.
        409 - A workspace with this name already exists.
        422 - Invalid workspace name.
        500 - Failed to update workspace name. Try again later.
    """
    try:
        managementService.updateWorkspaceName(workspaceId=workspaceId, newWorkspaceName=newWorkspaceName, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Workspace name updated successfully"
            }
        )
    except CustomException as e:
        raiseHttpException(e)

@router.delete("/deleteWorkspace/{workspaceId}")
async def deleteWorkspace(workspaceId: str, token = Depends(verifyToken)):
    """
    Delete a workspace and all its projects.

    Status Codes:
        200 - Success
        401 - Please login to delete workspace.
        404 - Workspace not found.
        500 - Failed to delete workspace. Try again later.
    """
    try:
        managementService.deleteWorkspace(workspaceId=workspaceId, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Workspace and all associated projects deleted successfully"
            }
        )
    except CustomException as e:
        raiseHttpException(e)

@router.patch("/updateBookmark")
async def updateBookmark(updateBookmarkDetails: UpdateProjectState, user: UserContext = Depends(verifyUser)):
    """
    Update the bookmark status of a project.

    Args:
        updateBookmarkDetails (UpdateProjectState): Details for updating bookmark status.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(updateBookmarkDetails.projectId, user.userId)
        await asyncio.to_thread(managementService.updateBookmark, updateBookmarkDetails = updateBookmarkDetails, userId = user.userId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project bookmark status updated successfully"})
    except CustomException as e:
        raiseHttpException(e)
    
@router.patch("/updateArchive")
async def updateArchive(updateArchiveDetails: UpdateProjectState, user: UserContext = Depends(verifyUser)):
    """
    Update the archive status of a project.

    Args:
        updateArchiveDetails (UpdateProjectState): Details for updating archive status.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(updateArchiveDetails.projectId, user.userId)
        await asyncio.to_thread(managementService.updateArchive, updateArchiveDetails = updateArchiveDetails, userId = user.userId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project archive status updated successfully"})
    except CustomException as e:
        raiseHttpException(e)
    
@router.patch("/updateTrash")
async def updateTrash(updateTrashDetails: UpdateProjectState, user: UserContext = Depends(verifyUser)):
    """
    Update the trash status of a project.

    Args:
        updateTrashDetails (UpdateProjectState): Details for updating trash status.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(updateTrashDetails.projectId, user.userId)
        await asyncio.to_thread(managementService.updateTrash, updateTrashDetails = updateTrashDetails, userId = user.userId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project trash status updated successfully"})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/generateMetadata/{projectId}")
async def generateMetadata(projectId: str, user: UserContext = Depends(requireCredits("metadata_generation")), userId: str = Depends(verifyProjectOwnership)):
    """
    Generate metadata for a given project.

    Args:
        projectId (str): The ID of the project.
        user: UserContext injected after credit validation.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Accepted status with Celery taskId.
    """
    try:
        task = celeryApp.send_task("NubrixAI.generateMetadata", args=[projectId, user.userId])
        return ORJSONResponse(status_code = 202, content = {"status": "ACCEPTED", "taskId": task.id})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/generateKpis/{projectId}")
async def generateKpis(projectId: str, preserveCharted: bool = False, user: UserContext = Depends(requireCredits("insight_generation")), userId: str = Depends(verifyProjectOwnership)):
    """
    Generate important KPIs for a given project.

    Args:
        projectId (str): The ID of the project.
        preserveCharted (bool): When true, existing charted KPIs are retained
            and only non-charted KPIs are regenerated. Defaults to false.
        user: UserContext injected after credit validation.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Accepted status with Celery taskId.
    """
    try:
        task = celeryApp.send_task("NubrixAI.generateInsights", args=[projectId, preserveCharted, user.userId])
        return ORJSONResponse(status_code = 202, content = {"status": "ACCEPTED", "taskId": task.id})
    except CustomException as e:
        raiseHttpException(e)
    
@router.get("/getInsights/{projectId}")
async def getInsights(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Retrieve insights and their status for a given project.

    Args:
        projectId (str): The ID of the project.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Project metadata or error message.
    """
    try:
        newJson = await asyncio.to_thread(managementService.getInsights, projectId = projectId)
        return ORJSONResponse(status_code = 200, content = newJson) 
    except CustomException as e:
        raiseHttpException(e)

@router.get("/getMetadata/{projectId}")
async def getMetadata(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Retrieve metadata for a given project.

    Args:
        projectId (str): The ID of the project.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Project metadata or error message.
    """
    try:
        newJson = await asyncio.to_thread(managementService.getMetadata, projectId = projectId)
        return ORJSONResponse(status_code = 200, content = newJson) 
    except CustomException as e:
        raiseHttpException(e)

@router.put("/editMetadata")
async def editMetadata(modifiedMetadata: EditMetadata, user: UserContext = Depends(verifyUser)):
    """
    Edit the metadata for a project.

    Args:
        modifiedMetadata (EditMetadata): The modified metadata details.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Updated metadata or error message.
    """
    try:
        await verifyProjectOwnershipDirect(modifiedMetadata.projectId, user.userId)
        jsonData = await asyncio.to_thread(managementService.editMetadata, modifiedMetadata = modifiedMetadata)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "metadata": jsonData})   
    except CustomException as e:
        raiseHttpException(e)

@router.delete("/deleteProject")
async def deleteProject(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Delete a project by its ID.

    Args:
        projectId (str): The ID of the project to delete.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await asyncio.to_thread(managementService.deleteProject, projectId = projectId, userId = userId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project deleted successfully"})
    except CustomException as e:
        raiseHttpException(e)

@router.patch("/renameProject")
async def renameProject(renameDetails: RenameProject, user: UserContext = Depends(verifyUser)):
    """
    Rename an existing project.

    Status Codes:
        200 - Success
        401 - Please login to rename the project.
        404 - Project not found.
        409 - A project with this name already exists in the workspace.
        422 - Invalid project name.
        500 - Failed to rename project. Try again later.
    """
    try:
        await verifyProjectOwnershipDirect(renameDetails.projectId, user.userId)
        await asyncio.to_thread(managementService.renameProject, renameDetails=renameDetails, token=user.token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Project renamed successfully"
            }
        )
    except CustomException as e:
        raiseHttpException(e)
    
@router.get("/listTriggers/{projectId}")
async def listTriggers(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    List all triggers for a given project.

    Args:
        projectId (str): The ID of the project.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: List of triggers or error message.
    """
    try:
        triggers = await asyncio.to_thread(managementService.listTriggers, projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "triggers": triggers})  
    except CustomException as e:
        raiseHttpException(e)
    
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
        allTriggers = await asyncio.to_thread(managementService.listTriggersUnderUserId, token = token)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "triggersAssignedToUser": allTriggers})   
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/generateReport/{projectId}")
async def generateReport(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Generate a report for a given project.

    Args:
        projectId (str): The ID of the project.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Accepted status with Celery taskId.
    """
    try:
        task = celeryApp.send_task("NubrixAI.generateReport", args=[projectId])
        return ORJSONResponse(status_code = 202, content = {"status": "ACCEPTED", "taskId": task.id})
    except CustomException as e:
        raiseHttpException(e)
    
@router.get("/getReport/{projectId}/{tableName}")
async def getReport(projectId: str, tableName: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Retrieve a report for a specific table in a project.

    Args:
        projectId (str): The ID of the project.
        tableName (str): The name of the table.
        userId: Verified user identifier.

    Returns:
        HTMLResponse: HTML content of the report or error message.
    """
    try:
        htmlContent = await asyncio.to_thread(managementService.getReport, projectId = projectId, tableName = tableName)
        return HTMLResponse(status_code = 200, content = htmlContent)  
    except CustomException as e:
        raiseHttpException(e)
    
@router.get("/getUserProfile")
async def getUserProfile(token = Depends(verifyToken)):
    """
    Retrieve user profile details.

    Status Codes:
        200 - Success
        401 - Please login to view profile.
        404 - User profile not found.
        500 - Failed to retrieve user profile. Try again later.
    """
    try:
        userProfile = managementService.getUserProfile(token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "profile": userProfile
            }
        )
    except CustomException as e:
        raiseHttpException(e)

@router.put("/editUserProfile")
async def editUserProfile(
    request: Request,
    userName: str | None = Form(default=None),
    company: str | None = Form(default=None),
    position: str | None = Form(default=None),
    bio: str | None = Form(default=None),
    token = Depends(verifyToken)
):
    """
    Update user profile details.

    Accepts form data with optional file upload for profile image.
    Profile image is stored in Supabase 'userProfileImages' bucket.

    Status Codes:
        200 - Success
        401 - Please login to edit profile.
        404 - User profile not found.
        500 - Failed to update user profile. Try again later.
    """
    try:
        form = await request.form()
        profileImageForm = form.get("profileImage")
        revertToDefault = False
        imageBytes = None
        imageFilename = None

        if profileImageForm == "":
            revertToDefault = True
        elif isinstance(profileImageForm, StarletteUploadFile):
            imageBytes = await profileImageForm.read()
            imageFilename = profileImageForm.filename
            if not imageBytes or not imageFilename:
                revertToDefault = True
        elif profileImageForm is not None:
            raise CustomException(
                ValueError("Invalid profileImage form value"),
                statusCode=422,
                uiMessage="profileImage must be a file upload or an empty string."
            )

        updatedProfile = managementService.editUserProfile(
            userName=userName,
            company=company,
            position=position,
            bio=bio,
            profileImage=imageBytes,
            profileImageFilename=imageFilename,
            token=token,
            revertToDefault=revertToDefault
        )
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Profile updated successfully",
                "profile": updatedProfile
            }
        )
    except CustomException as e:
        raiseHttpException(e)
