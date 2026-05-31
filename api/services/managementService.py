"""
managementService.py

This module provides the ManagementService class, which encapsulates business logic for project management, metadata generation, editing, deletion, and report generation for AnalyticsHub projects. It interacts with the Supabase client and manages project records, metadata, and reports in storage.
"""
__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["managementService"] 


from analyticsHub.components.metadataGenerator import MetadataGenerator
from analyticsHub.components.insightGenerator import InsightGenerator
from analyticsHub.components.reportGenerator import ReportGenerator
from analyticsHub.components.domainKpiMapper import DomainKpiMapper
from utils.llmOutputParser import parseModelJsonOutput
from api.commons import updateProjectModifiedAt
from utils.exceptionHandler import CustomException
from api.services.subscriptions.subscriptionFieldUtils import (
    CANONICAL_SUBSCRIPTION_SELECT,
    subscriptionExperts,
    toApiPlanFields,
)
from api.services.subscriptions.paymentValidationService import (
    calculateSubscriptionDaysLeft,
    mergeSubscriptionLifecycleSnapshot,
)
from concurrent.futures import ProcessPoolExecutor
from utils.logger import logger
from urllib.request import urlopen
from api.commons import client
from api.models import (
    UpdateProjectState,
    CreateProject,
    EditMetadata,
    RenameProject
)
from jose import jwt
import pandas as pd
import datetime
import orjson
import json
import uuid
import time
import os
import io

class ManagementService:
    """
    Service class for managing projects, metadata, and reports.

    Handles creation, listing, updating, and deletion of projects; generation and editing of metadata; management of bookmarks, archives, and trash; and report generation and retrieval. Interacts with the Supabase client and manages project records, metadata, and reports in storage.
    """
    def __init__(self) -> None:
        """
        Initializes the ManagementService, sets up the metadata and report generators, and the Supabase client.
        """
        logger.info("Initializing Authentication Service.")
        self.metadataGenerator = MetadataGenerator()
        self.insightGenerator = InsightGenerator()
        self.reportGenerator = ReportGenerator()
        self.domainKpiMapper = DomainKpiMapper()
        self.client = client

    def createProject(self, projectDetails: CreateProject, token: str) -> str:
        """
        Create a new project.
        Raises:
            CustomException:
                401 - User not authenticated
                409 - Project already exists in workspace
                422 - Invalid project details
                500 - Project creation failure
        """
        try:
            if not projectDetails.projectName or not projectDetails.workspaceId or not projectDetails.domainExpert:
                raise CustomException(
                    ValueError("Invalid project details"),
                    statusCode=422,
                    uiMessage="Invalid project details."
                )
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            existingProjects = self.client.table("Projects") \
                .select("projectName") \
                .eq("workspaceId", projectDetails.workspaceId) \
                .neq("isTrash", 1) \
                .execute().data
            if projectDetails.projectName in [x.get("projectName") for x in existingProjects]:
                raise CustomException(
                    ValueError("Duplicate project"),
                    statusCode=409,
                    uiMessage="A project with this name already exists in the workspace."
                )
            projectId = str(uuid.uuid4())
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.client.table("Projects").insert({
                "projectId": projectId,
                "projectName": projectDetails.projectName,
                "projectDescription": projectDetails.projectDescription,
                "ownerUserId": decodedToken["userId"],
                "ownerUserMail": decodedToken["email"],
                "workspaceId": projectDetails.workspaceId,
                "domainExpert": projectDetails.domainExpert,
                "modifiedAt": now
            }).execute()
            return projectId
        except jwt.ExpiredSignatureError:
            raise CustomException(
                ValueError("Unauthenticated"),
                statusCode=401,
                uiMessage="Please login to create a project."
            )
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Failed to create project. Try again later."
            )
            logger.error(exception)
            raise exception
        
    def createWorkspace(self, workspaceName: str, token: str) -> str:
        """
        Create a new workspace.
        Raises:
            CustomException:
                401 - User not authenticated
                409 - Workspace already exists
                422 - Invalid workspace name
                500 - Workspace creation failure
        """
        try:
            if not workspaceName:
                raise CustomException(
                    ValueError("Invalid workspace name"),
                    statusCode=422,
                    uiMessage="Invalid workspace name."
                )
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            existingWorkspaces = self.client.table("Workspaces") \
                .select("workspaceName") \
                .eq("ownerId", decodedToken["userId"]) \
                .execute().data
            if workspaceName in [x.get("workspaceName") for x in existingWorkspaces]:
                raise CustomException(
                    ValueError("Duplicate workspace"),
                    statusCode=409,
                    uiMessage="Workspace with this name already exists."
                )
            workspaceId = str(uuid.uuid4())
            self.client.table("Workspaces").insert({
                "id": workspaceId,
                "ownerId": decodedToken["userId"],
                "ownerEmail": decodedToken["email"],
                "workspaceName": workspaceName
            }).execute()
            return workspaceId
        except jwt.ExpiredSignatureError:
            raise CustomException(
                ValueError("Unauthenticated"),
                statusCode=401,
                uiMessage="Please login to create a workspace."
            )
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Unable to create workspace. Try again later."
            )
            logger.error(exception)
            raise exception
        
    def listProjects(self, workspaceId: str, token: str) -> pd.DataFrame:
        """
        List all projects owned by the user associated with the provided token.

        Args:
            workspaceId (str): workspaceId for which the projects needs to be listed.
            token (str): JWT token for user authentication.

        Returns:
            pd.DataFrame: DataFrame containing the user's projects.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            data = pd.DataFrame(self.client.table("Projects").select("*").execute().data)
            if len(data) == 0:
                return data
            else:
                data = data[(data["ownerUserId"] == decodedToken["userId"]) & (data["workspaceId"] == workspaceId)]
                return data
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def listWokspaces(self, token: str) -> pd.DataFrame:
        """
        List all workspaces owned by the user associated with the provided token.

        Args:
            token (str): JWT token for user authentication.

        Returns:
            pd.DataFrame: DataFrame containing the user's workspaces.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            data = pd.DataFrame(self.client.table("Workspaces").select("*").execute().data)
            data = data[data["ownerId"] == decodedToken["userId"]]
            subscription = self._getCanonicalSubscription(decodedToken["userId"])
            subscribedExperts = subscriptionExperts(subscription)
            response = {
                "workspaces": data.to_dict(orient = "records"),
                "aiExperts": {
                    "subscribedExperts": subscribedExperts,
                    "allExperts": ["banking", "manufacturing", "supplychain", "telecom"]
                }
            }
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
    
    def updateCurrentWorkspace(self, updatedWorkspaceId: str, token: str) -> None:
        """
        Update the current Workspace ID of a user.

        Args:
            updatedWorkspaceId (str): Details specifying the updated workspace id.
            token (str): Token for extracting the user id.

        Raises:
            CustomException: For any errors during update.        
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            _ = self.client.table("Users").update({"currentWorkspaceId": updatedWorkspaceId}).eq("userId", decodedToken["userId"]).execute()   
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def updateWorkspaceName(self, workspaceId: str, newWorkspaceName: str, token: str) -> None:
        """
        Update the name of a workspace.

        Args:
            workspaceId (str): The ID of the workspace to rename.
            newWorkspaceName (str): The new name for the workspace.
            token (str): JWT token for user authentication.

        Raises:
            CustomException:
                401 - User not authenticated
                404 - Workspace not found
                409 - Workspace with new name already exists
                422 - Invalid workspace name
                500 - Workspace update failure
        """
        try:
            if not newWorkspaceName:
                raise CustomException(
                    ValueError("Invalid workspace name"),
                    statusCode=422,
                    uiMessage="Invalid workspace name."
                )
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            existingWorkspace = self.client.table("Workspaces") \
                .select("*") \
                .eq("id", workspaceId) \
                .eq("ownerId", decodedToken["userId"]) \
                .execute().data
            if not existingWorkspace:
                raise CustomException(
                    ValueError("Workspace not found"),
                    statusCode=404,
                    uiMessage="Workspace not found."
                )
            existingWorkspaces = self.client.table("Workspaces") \
                .select("workspaceName") \
                .eq("ownerId", decodedToken["userId"]) \
                .execute().data
            if newWorkspaceName in [x.get("workspaceName") for x in existingWorkspaces]:
                raise CustomException(
                    ValueError("Duplicate workspace name"),
                    statusCode=409,
                    uiMessage="A workspace with this name already exists."
                )
            _ = self.client.table("Workspaces").update({"workspaceName": newWorkspaceName}).eq("id", workspaceId).execute()
            return
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Failed to update workspace name. Try again later."
            )
            logger.error(exception)
            raise exception
        
    def deleteWorkspace(self, workspaceId: str, token: str) -> None:
        """
        Delete a workspace and all associated projects and files.

        Args:
            workspaceId (str): The ID of the workspace to delete.
            token (str): JWT token for user authentication.

        Raises:
            CustomException:
                401 - User not authenticated
                404 - Workspace not found
                500 - Workspace deletion failure
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            existingWorkspace = self.client.table("Workspaces") \
                .select("*") \
                .eq("id", workspaceId) \
                .eq("ownerId", decodedToken["userId"]) \
                .execute().data
            if not existingWorkspace:
                raise CustomException(
                    ValueError("Workspace not found"),
                    statusCode=404,
                    uiMessage="Workspace not found."
                )
            projects = self.client.table("Projects") \
                .select("projectId") \
                .eq("workspaceId", workspaceId) \
                .execute().data
            for project in projects:
                self.deleteProject(projectId=project.get("projectId"))
            _ = self.client.table("Workspaces").delete().eq("id", workspaceId).execute()
            return
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Failed to delete workspace. Try again later."
            )
            logger.error(exception)
            raise exception
        
    def updateBookmark(self, updateBookmarkDetails: UpdateProjectState) -> None:
        """
        Update the bookmark state of a project.

        Args:
            updateBookmarkDetails (UpdateProjectState): Details specifying the project and action (add/remove).

        Raises:
            CustomException: For any errors during update.
        """
        try:
            if updateBookmarkDetails.action == "add":
                _ = self.client.table("Projects").update({"isBookmarked": 1}).eq("projectId", updateBookmarkDetails.projectId).execute()
            else:
                _ = self.client.table("Projects").update({"isBookmarked": 0}).eq("projectId", updateBookmarkDetails.projectId).execute()   
            updateProjectModifiedAt(updateBookmarkDetails.projectId)
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   

    def updateArchive(self, updateArchiveDetails: UpdateProjectState) -> None:
        """
        Update the archive state of a project.

        Args:
            updateArchiveDetails (UpdateProjectState): Details specifying the project and action (add/remove).

        Raises:
            CustomException: For any errors during update.
        """
        try:
            if updateArchiveDetails.action == "add":
                _ = self.client.table("Projects").update({"isArchived": 1}).eq("projectId", updateArchiveDetails.projectId).execute()
            else:
                _ = self.client.table("Projects").update({"isArchived": 0}).eq("projectId", updateArchiveDetails.projectId).execute()    
            updateProjectModifiedAt(updateArchiveDetails.projectId)
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception  

    def updateTrash(self, updateTrashDetails: UpdateProjectState) -> None:
        """
        Update the trash state of a project.

        Args:
            updateTrashDetails (UpdateProjectState): Details specifying the project and action (add/remove).

        Raises:
            CustomException: For any errors during update.
        """
        try:
            if updateTrashDetails.action == "add":
                _ = self.client.table("Projects").update({"isTrash": 1}).eq("projectId", updateTrashDetails.projectId).execute()
            else:
                _ = self.client.table("Projects").update({"isTrash": 0}).eq("projectId", updateTrashDetails.projectId).execute()    
            updateProjectModifiedAt(updateTrashDetails.projectId)
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def _attributeInfoFunc(self, projectId: str, dataframeName: str) -> str:
        """
        Generate attribute information string for a given dataframe in a project.

        Args:
            projectId (str): The project identifier.
            dataframeName (str): The dataframe/table name.

        Returns:
            str: Attribute information string for the dataframe.
        """
        df = pd.read_parquet(os.environ["FILE_URL"].format(projectId = projectId, fileName = dataframeName))
        attributeInfo = f'DATAFRAME NAME: {dataframeName}\n'
        for column in df.columns: attributeInfo += '- ' + str(column) + ' (' + df.get(column).dtype.name + ')\n'
        attributeInfo += 'SHAPE: ' + str(df.shape) + '\n'
        attributeInfo += 'SAMPLE ROW:\n' + str(df.loc[df.index[:1]].to_string()) + '\n'
        return attributeInfo

    def _parseModelJsonOutput(self, rawOutput: object, stage: str) -> dict:
        """Delegate to shared parser in utils.llmOutputParser."""
        return parseModelJsonOutput(rawOutput, stage)
        
    def _generateMetadata(self, projectId: str) -> dict:
        """
        Generate metadata for all data files in a project.

        Args:
            projectId (str): The project identifier.

        Returns:
            dict: Generated metadata dictionary.

        Raises:
            CustomException: For any errors during metadata generation.
        """
        try:
            dataFiles = [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = projectId) if x.get("name").endswith(".parquet")]
            results = ""
            for fileName in dataFiles:
                dataframeName = fileName.replace(".parquet", "")
                results += self._attributeInfoFunc(projectId = projectId, dataframeName = dataframeName)
            metadataChain = self.metadataGenerator.getMetadataChain()
            metadataRaw = metadataChain.invoke({"metadata": results})
            metadata = self._parseModelJsonOutput(
                rawOutput=metadataRaw,
                stage="Metadata generation"
            )
            return metadata
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)
        
    def generateInsightsForProject(self, projectId: str) -> dict:
        """
        Generate insights for the project from its metadata and also determine the most important KPIs that can be derived from it.

        Args:
            projectId (str): The project identifier.

        Returns:
            dict: A dictionary containing generated insights.
        """
        try:
            fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            domainFile = self.client.table("Projects").select("domainExpert").eq("projectId", projectId).execute().data[0].get("domainExpert") + ".json"
            metadata = json.loads(urlopen(fileUrl).read())
            if domainFile in [x.get("name") for x in self.client.storage.from_("DomainSpecificKpis").list()]:
                domainFileUrl = os.environ["DOMAIN_FILE_URL"].format(fileName = domainFile) + f"?cb={int(time.time())}"
                domainData = json.loads(urlopen(domainFileUrl).read())
                domainKpiMapperChain = self.domainKpiMapper.getDomainKpiMapperChain()
                domainKpiInsightsRaw = domainKpiMapperChain.invoke(
                    {"domainProfile": domainData, "metadata": metadata}
                )
                domainKpiInsights = self._parseModelJsonOutput(
                    rawOutput=domainKpiInsightsRaw,
                    stage="Domain KPI mapping"
                )
                overlapKpis = [str(value) for value in domainKpiInsights.values() if value is not None]
            else:
                overlapKpis = list()
            insightGeneratorChain = self.insightGenerator.getInsightGeneratorChain()
            insightsRaw = insightGeneratorChain.invoke({"metadata": metadata})
            insights = self._parseModelJsonOutput(
                rawOutput=insightsRaw,
                stage="Insight generation"
            )
            allInsights, counterValue = list(), 1
            for kpi in overlapKpis:
                insightDict = {"id": counterValue, "query": kpi, "isCharted": False}
                allInsights.append(insightDict)
                counterValue += 1
            for insightKey in insights.keys():
                insightText = insights.get(insightKey)
                if insightText is None:
                    continue
                insightDict = {"id": counterValue, "query": str(insightText), "isCharted": False}
                allInsights.append(insightDict)
                counterValue += 1
            insights = {"insights": allInsights}
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(insights, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(path = f"{projectId}/insights.json", file = buffer.getvalue(), file_options = {"upsert": "true"})    
            updateProjectModifiedAt(projectId)
            return insights
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)
        
    def getInsights(self, projectId: str) -> dict:
        """
        Retrieve insights for a project.

        Args:
            projectId (str): The project identifier.

        Returns:
            dict: Dictionary containing insights for in the project.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "insights.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            jsonData = json.loads(urlopen(fileUrl).read())
            return jsonData
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)

    def generateMetadata(self, projectId: str) -> dict:
        """
        Generate or update metadata for a project, uploading it to storage, and generating insights.

        Args:
            projectId (str): The project identifier.

        Returns:
            dict: The updated metadata dictionary with important insights.

        Raises:
            CustomException: For any errors during metadata generation or upload.
        """
        try:
            files = self.client.storage.from_("AnalyticsHub").list(projectId)
            filenames = [x.get("name") for x in files]
            if "metadata.json" in filenames:
                fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                jsonData = json.loads(urlopen(fileUrl).read())
                newMetadata = self._generateMetadata(projectId = projectId)
                dataFiles = list()
                for file in files:
                    if file["name"] == "metadata.json":
                        metadataLastModifiedTime = datetime.datetime.strptime(file["metadata"]["lastModified"], '%Y-%m-%dT%H:%M:%S.000Z')
                    elif os.path.splitext(file["name"])[-1] == ".parquet":
                        dataFiles.append(file)
                    else:
                        continue
                updatedFiles = filter(lambda x: datetime.datetime.strptime(x["metadata"]["lastModified"], '%Y-%m-%dT%H:%M:%S.000Z') > metadataLastModifiedTime, dataFiles)
                updatedFiles = [os.path.splitext(x.get("name"))[0] for x in updatedFiles]
                if updatedFiles != []:
                    for key in updatedFiles: jsonData[key] = newMetadata[key]
                else:
                    pass
            else:
                jsonData = self._generateMetadata(projectId = projectId)
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(jsonData, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(path = f"{projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})     
            updateProjectModifiedAt(projectId)
            return jsonData
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def getMetadata(self, projectId: str) -> dict:
        """
        Retrieve metadata for a project.

        Args:
            projectId (str): The project identifier.

        Returns:
            dict: Dictionary containing metadata for all tables in the project.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            files = self.client.storage.from_("AnalyticsHub").list(projectId)
            filenames = [x.get("name") for x in files]
            if "metadata.json" not in filenames:
                return dict()
            else:
                fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                jsonData = json.loads(urlopen(fileUrl).read())
                newJson = {"tables": []}
                for key in jsonData:
                    tableJson = {
                        "tableName": key,
                        "tableDesc": jsonData.get(key).get("description"),
                        "shape": jsonData.get(key).get("shape"),
                        "columns": jsonData.get(key).get("columns")
                    }
                    newJson.get("tables").append(tableJson)
                return newJson
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def editMetadata(self, modifiedMetadata: EditMetadata) -> dict:
        """
        Edit table or column metadata for a project.

        Args:
            modifiedMetadata (EditMetadata): Details specifying the project, table, and metadata changes.

        Returns:
            dict: The updated metadata dictionary.

        Raises:
            CustomException: For any errors during update.
        """
        try:
            fileUrl = os.environ["FILE_URL"].format(projectId = modifiedMetadata.projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            jsonData = json.loads(urlopen(fileUrl).read())
            if modifiedMetadata.tableDescription and not (modifiedMetadata.columnName and modifiedMetadata.columnDescription):
                jsonData[modifiedMetadata.tableName]["description"] = modifiedMetadata.tableDescription
            elif (modifiedMetadata.columnName and modifiedMetadata.columnDescription) and not modifiedMetadata.tableDescription:
                columns = jsonData[modifiedMetadata.tableName]["columns"]
                for column in columns:
                    if column["name"] == modifiedMetadata.columnName:
                        idx = columns.index(column)
                columns[idx]["description"] = modifiedMetadata.columnDescription
                jsonData[modifiedMetadata.tableName]["columns"] = columns
            else:
                raise AttributeError("Invalid combination of parameters provided")
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(jsonData, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(path = f"{modifiedMetadata.projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            updateProjectModifiedAt(modifiedMetadata.projectId)
            return jsonData
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def deleteProject(self, projectId: str) -> None:
        """
        Delete a project and all associated files from storage.

        Args:
            projectId (str): The project identifier.

        Raises:
            CustomException: For any errors during deletion.
        """
        try:
            _ = self.client.table("Projects").delete().eq("projectId", projectId).execute()
            allFiles = self.client.storage.from_("AnalyticsHub").list(projectId)
            fileNames = [os.path.join(projectId, x.get("name")) for x in allFiles]
            if fileNames:
                _ = self.client.storage.from_("AnalyticsHub").remove(fileNames)
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   

    def listTriggers(self, projectId: str) -> list:
        """
        List all triggers associated with a project.

        Args:
            projectId (str): The project identifier.

        Returns:
            list: List of triggers for the project.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            searchResult = list(filter(lambda x: True if x["projectId"] == projectId else False, self.client.table("Projects").select("*").execute().data))[0]
            triggers = searchResult.get("triggers")
            if triggers:
                triggers = triggers.split(", ")
            else:
                triggers = list()
            return triggers
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def listTriggersUnderUserId(self, token: str) -> list:
        """
        List all triggers associated with the user identified by the provided token.

        Args:
            token (str): JWT token for user authentication.

        Returns:
            list: List of triggers for the user.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            userId = jwt.decode(token = token, key = os.environ["SECRET_KEY"], algorithms = ["HS256"]).get("userId")
            allTriggers = list()
            allProjects = self.client.table("Projects").select("*").execute().data or []
            for project in allProjects:
                if project.get("ownerUserId") != userId:
                    continue
                triggers = project.get("triggers")
                if not triggers or pd.isna(triggers):
                    continue
                allTriggers += [
                    trigger.strip()
                    for trigger in str(triggers).split(",")
                    if trigger.strip()
                ]
            return allTriggers
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def generateReport(self, projectId: str) -> dict:
        """
        Generate profiling reports for all tables in a project and upload them to storage.

        Args:
            projectId (str): The project identifier.

        Raises:
            CustomException: For any errors during report generation or upload.
        """
        try:
            allTables = self.reportGenerator.getAllTables(projectId = projectId)
            with ProcessPoolExecutor(max_workers = 5) as executor:
                futures = [executor.submit(self.reportGenerator.getProfilingReport, projectId, tableName) for tableName in allTables]
            results = [x.result() for x in futures]
            dct = dict(zip(allTables, results))
            with io.BytesIO() as buffer:
                output = orjson.dumps(dct)
                buffer.write(output)
                buffer.seek(0)
                _ = self.client.storage.from_("AnalyticsHub").upload(path = f"{projectId}/generatedReport.json", file = buffer.getvalue(), file_options = {"upsert": "true"})  
            updateProjectModifiedAt(projectId)
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def getReport(self, projectId: str, tableName: str) -> str:
        """
        Retrieve the profiling report HTML content for a specific table in a project.

        Args:
            projectId (str): The project identifier.
            tableName (str): The table name.

        Returns:
            str: HTML content of the profiling report for the table.

        Raises:
            CustomException: For any errors during retrieval.
        """
        try:
            fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "generatedReport.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            jsonData = json.loads(urlopen(fileUrl).read())
            htmlContent = jsonData.get(tableName)
            return htmlContent
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   
        
    def renameProject(self, renameDetails: RenameProject, token: str) -> None:
        """
        Rename an existing project.

        Args:
            renameDetails (RenameProject): Details containing the project ID and new project name.
            token (str): JWT token for user authentication.

        Raises:
            CustomException:
                401 - User not authenticated
                404 - Project not found
                409 - A project with the new name already exists in the workspace
                422 - Invalid project name
                500 - Project rename failure
        """
        try:
            if not renameDetails.newProjectName:
                raise CustomException(
                    ValueError("Invalid project name"),
                    statusCode=422,
                    uiMessage="Invalid project name."
                )
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            existingProject = self.client.table("Projects") \
                .select("*") \
                .eq("projectId", renameDetails.projectId) \
                .eq("ownerUserId", decodedToken["userId"]) \
                .execute().data
            if not existingProject:
                raise CustomException(
                    ValueError("Project not found"),
                    statusCode=404,
                    uiMessage="Project not found."
                )
            workspaceId = existingProject[0].get("workspaceId")
            existingProjects = self.client.table("Projects") \
                .select("projectName") \
                .eq("workspaceId", workspaceId) \
                .neq("isTrash", 1) \
                .execute().data
            if renameDetails.newProjectName in [x.get("projectName") for x in existingProjects]:
                raise CustomException(
                    ValueError("Duplicate project name"),
                    statusCode=409,
                    uiMessage="A project with this name already exists in the workspace."
                )
            _ = self.client.table("Projects").update({"projectName": renameDetails.newProjectName}).eq("projectId", renameDetails.projectId).execute()
            updateProjectModifiedAt(renameDetails.projectId)
            return
        except jwt.ExpiredSignatureError:
            raise CustomException(
                ValueError("Unauthenticated"),
                statusCode=401,
                uiMessage="Please login to rename the project."
            )
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Failed to rename project. Try again later."
            )
            logger.error(exception)
            raise exception

    @staticmethod
    def _mapSubscriptionStatusForProfile(status: str | None) -> str:
        normalized = (status or "").strip().lower()
        mapping = {
            "none": "NONE",
            "trial": "TRIAL",
            "active": "ACTIVE",
            "renewal_upcoming": "ACTIVE",
            "payment_pending": "ACTIVE",
            "past_due": "PAUSED",
            "suspended": "PAUSED",
            "cancelled": "CANCELLED",
            "expired": "EXPIRED",
        }
        return mapping.get(normalized, "NONE")

    @staticmethod
    def _mapBillingModeToPlanType(billingMode: str | None, status: str | None = None) -> str:
        normalizedStatus = (status or "").strip().lower()
        if normalizedStatus == "trial":
            return "free"
        if normalizedStatus == "none":
            return "none"
        if billingMode == "none":
            return "none"
        if billingMode == "monthly_recurring":
            return "pro"
        if billingMode == "annual_prepaid":
            return "annual"
        return "none"

    def _getCanonicalSubscription(self, userId: str) -> dict:
        subscription = self.client.table("subscriptions") \
            .select(CANONICAL_SUBSCRIPTION_SELECT) \
            .eq("user_id", userId) \
            .order("updated_at", desc=True) \
            .limit(1) \
            .execute().data
        if not subscription:
            raise CustomException(
                ValueError("Missing subscription row"),
                statusCode=409,
                uiMessage="Subscription data is not available. Please contact support."
            )
        return subscription[0]

    def _refreshLifecycleSnapshot(self, userId: str, subscription: dict) -> int:
        expiryStr = subscription.get("current_period_end")
        daysLeft = calculateSubscriptionDaysLeft(expiryStr)
        billingState = mergeSubscriptionLifecycleSnapshot(
            subscription.get("billing_state"),
            currentPeriodEnd=expiryStr,
            status=subscription.get("status"),
        )
        try:
            self.client.table("subscriptions").update({
                "billing_state": billingState
            }).eq("user_id", userId).execute()
            subscription["billing_state"] = billingState
        except Exception as e:
            logger.warning(f"Failed to update lifecycle snapshot for user {userId}: {e}")
        return daysLeft
	        
    def getUserProfile(self, token: str) -> dict:
        """
        Retrieve user profile details from the Users table.

        Args:
            token (str): JWT token for user authentication.

        Returns:
            dict: Dictionary containing user profile fields.

        Raises:
            CustomException:
                401 - User not authenticated
                404 - User not found
                500 - Failed to retrieve user profile
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            userId = decodedToken["userId"]
            userRecord = self.client.table("Users") \
                .select(
                    "userId",
                    "fullName",
                    "email",
                    "profileImage",
                    "companyName",
                    "role",
	                    "profileBio"
                ) \
                .eq("userId", userId) \
                .execute().data
            if not userRecord:
                raise CustomException(
                    ValueError("User not found"),
                    statusCode=404,
                    uiMessage="User profile not found."
                )
            record = userRecord[0]
            subscription = self._getCanonicalSubscription(userId)
            planFields = toApiPlanFields(subscription)
            expiryStr = subscription.get("current_period_end")
            if expiryStr == "None":
                expiryStr = None
            subscriptionDaysLeft = self._refreshLifecycleSnapshot(userId, subscription)
            currentStatus = self._mapSubscriptionStatusForProfile(subscription.get("status"))
            nextBilling = None
            if currentStatus == "ACTIVE":
                nextBilling = subscription.get("renewal_due_at") or expiryStr
            profileResponse = {
                "userId": record.get("userId"),
                "userName": record.get("fullName"),
                "email": record.get("email"),
                "profileImg": record.get("profileImage"),
                "company": record.get("companyName"),
                "position": record.get("role"),
                "bio": record.get("profileBio"),
                "plan": {
                    "planType": self._mapBillingModeToPlanType(
                        subscription.get("billing_mode"),
                        subscription.get("status"),
                    ),
                    "status": currentStatus,
                    "planExpire": expiryStr,
                    "nextBilling": nextBilling,
	                    "subscribedExperts": planFields["subscribedExperts"],
	                    "domainCount": planFields["domainCount"],
	                    "pendingRemovals": planFields["pendingRemovals"],
                    "subscriptionDaysLeft": subscriptionDaysLeft,
                    "billingMode": subscription.get("billing_mode"),
                    "renewalDueAt": subscription.get("renewal_due_at"),
                }
            }
            return profileResponse
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Failed to retrieve user profile. Try again later."
            )
            logger.error(exception)
            raise exception
        
    def editUserProfile(
        self,
        userName: str | None,
        company: str | None,
        position: str | None,
        bio: str | None,
        profileImage: bytes | None,
        profileImageFilename: str | None,
        token: str
    ) -> dict:
        """
        Update user profile details in the Users table. Optionally upload profile image to Supabase storage.

        Args:
            userName (str | None): User's display name.
            company (str | None): User's company name.
            position (str | None): User's position/role.
            bio (str | None): User's profile bio.
            profileImage (bytes | None): Raw bytes of the profile image file, or None if not uploading.
            profileImageFilename (str | None): Original filename of the uploaded image, or None.
            token (str): JWT token for user authentication.

        Returns:
            dict: Dictionary containing the updated user profile fields.

        Raises:
            CustomException:
                401 - User not authenticated
                404 - User not found
                500 - Failed to update user profile
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            userId = decodedToken["userId"]
            existingUser = self.client.table("Users") \
                .select("userId") \
                .eq("userId", userId) \
                .execute().data
            if not existingUser:
                raise CustomException(
                    ValueError("User not found"),
                    statusCode=404,
                    uiMessage="User profile not found."
                )
            updateData = {}
            if userName is not None:
                updateData["fullName"] = userName
            if company is not None:
                updateData["companyName"] = company
            if position is not None:
                updateData["role"] = position
            if bio is not None:
                updateData["profileBio"] = bio
            if profileImage and profileImageFilename:
                fileExtension = os.path.splitext(profileImageFilename)[-1]
                storagePath = f"{userId}{fileExtension}"
                self.client.storage.from_("userProfileImages").upload(
                    path=storagePath,
                    file=profileImage,
                    file_options={"upsert": "true"}
                )
                profileImageUrl = f"{os.environ['SUPABASE_URL']}/storage/v1/object/public/userProfileImages/{storagePath}"
                updateData["profileImage"] = profileImageUrl
            if updateData:
                self.client.table("Users").update(updateData).eq("userId", userId).execute()
            updatedUserRecord = self.client.table("Users") \
                .select(
                    "userId",
                    "fullName",
                    "email",
                    "profileImage",
                    "companyName",
                    "role",
	                    "profileBio"
                ) \
                .eq("userId", userId) \
                .execute().data
            record = updatedUserRecord[0]
            subscription = self._getCanonicalSubscription(userId)
            planFields = toApiPlanFields(subscription)
            expiryStr = subscription.get("current_period_end")
            subscriptionDaysLeft = self._refreshLifecycleSnapshot(userId, subscription)
            currentStatus = self._mapSubscriptionStatusForProfile(subscription.get("status"))
            nextBilling = None
            if currentStatus == "ACTIVE":
                nextBilling = subscription.get("renewal_due_at") or expiryStr
            profileResponse = {
                "userId": record.get("userId"),
                "userName": record.get("fullName"),
                "email": record.get("email"),
                "profileImg": record.get("profileImage"),
                "company": record.get("companyName"),
                "position": record.get("role"),
                "bio": record.get("profileBio"),
                "plan": {
                    "planType": self._mapBillingModeToPlanType(
                        subscription.get("billing_mode"),
                        subscription.get("status"),
                    ),
                    "status": currentStatus,
                    "planExpire": expiryStr,
                    "nextBilling": nextBilling,
	                    "subscribedExperts": planFields["subscribedExperts"],
	                    "domainCount": planFields["domainCount"],
	                    "pendingRemovals": planFields["pendingRemovals"],
                    "subscriptionDaysLeft": subscriptionDaysLeft,
                    "billingMode": subscription.get("billing_mode"),
                    "renewalDueAt": subscription.get("renewal_due_at"),
                }
            }
            return profileResponse
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Failed to update user profile. Try again later."
            )
            logger.error(exception)
            raise exception

managementService = ManagementService()
