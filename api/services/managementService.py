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
from utils.exceptionHandler import CustomException
from concurrent.futures import ProcessPoolExecutor
from utils.logger import logger
from urllib.request import urlopen
from api.commons import client
from api.models import (
    UpdateProjectState,
    CreateProject,
    EditMetadata
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
                .execute().data
            if projectDetails.projectName in [x.get("projectName") for x in existingProjects]:
                raise CustomException(
                    ValueError("Duplicate project"),
                    statusCode=409,
                    uiMessage="A project with this name already exists in the workspace."
                )
            projectId = str(uuid.uuid4())
            self.client.table("Projects").insert({
                "projectId": projectId,
                "projectName": projectDetails.projectName,
                "projectDescription": projectDetails.projectDescription,
                "ownerUserId": decodedToken["userId"],
                "ownerUserMail": decodedToken["email"],
                "workspaceId": projectDetails.workspaceId,
                "domainExpert": projectDetails.domainExpert
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
            subscribedExperts = [x.strip() for x in self.client.table("Users").select("subscribedExperts").eq("userId", decodedToken["userId"]).execute().data[0]["subscribedExperts"].split(",")]
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
            metadata = metadataChain.invoke({"metadata": results})
            metadataParts = metadata.split("```")
            metadata = metadataParts[-2]
            metadata = orjson.loads("\n".join(metadata.split("\n")[1:]).encode())
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
                domainKpiInsights = domainKpiMapperChain.invoke({"domainProfile": domainData, "metadata": metadata})
                domainKpiInsightParts = domainKpiInsights.split("```")
                domainKpiInsights = domainKpiInsightParts[-2]
                domainKpiInsights = orjson.loads("\n".join(domainKpiInsights.split("\n")[1:]).encode("utf-8"))
                overlapKpis = list(domainKpiInsights.values())
            else:
                overlapKpis = list()
            insightGeneratorChain = self.insightGenerator.getInsightGeneratorChain()
            insights = insightGeneratorChain.invoke({"metadata": metadata})
            insightsParts = insights.split("```")
            insights = insightsParts[-2]
            insights = orjson.loads("\n".join(insights.split("\n")[1:]).encode("utf-8"))
            allInsights, counterValue = list(), 1
            for kpi in overlapKpis:
                insightDict = {"id": counterValue, "query": kpi, "isCharted": False}
                allInsights.append(insightDict)
                counterValue += 1
            for insightKey in insights.keys():
                insightDict = {"id": counterValue, "query": insights.get(insightKey), "isCharted": False}
                allInsights.append(insightDict)
                counterValue += 1
            insights = {"insights": allInsights}
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(insights, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(path = f"{projectId}/insights.json", file = buffer.getvalue(), file_options = {"upsert": "true"})    
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
            allFiles = client.storage.from_("AnalyticsHub").list(projectId)
            fileNames = [os.path.join(projectId, x.get("name")) for x in allFiles]
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
            allProjects = pd.DataFrame(client.table("Projects").select("*").execute().data)
            allProjects = allProjects[allProjects["ownerUserId"] == userId]
            allTriggers = list()
            if allProjects["triggers"].isna().all():
                pass
            else:
                for trigger in allProjects["triggers"]:
                    allTriggers += trigger.split(", ")
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

managementService = ManagementService()