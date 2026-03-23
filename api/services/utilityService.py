"""
UtilityService module provides utility functions such as speech-to-text transcription,
hybrid image-to-insights generation, insight persistence, sending forecasts,
and sample data manipulations for AnalyticsHub.

Author: Rauhan Ahmed Siddiqui
Version: 1.0.0
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["utilityService"]


from analyticsHub.components.insightContextBuilder import InsightContextBuilder
from analyticsHub.components.imageToInsights import ImageToInsights
from analyticsHub.components.signalEngine import SignalEngine
from analyticsHub.components.speechToText import SpeechToText
from api.models import SpeechToTextModel, ImageToInsightsModel
from utils.exceptionHandler import CustomException
from analyticsHub.triggers.celery import celeryApp
from urllib.request import urlopen
from celery.result import AsyncResult
from utils.logger import logger
from api.commons import client
from datetime import datetime, timezone
import seaborn as sns
import pandas as pd
import json
import uuid
import time
import os
import io

VALID_INSIGHT_STATUSES = {"new", "accepted", "rejected", "implemented"}

class UtilityService:
    """
    Service class providing utility operations such as speech transcription, hybrid insight
    generation with persistence, sending forecasts, and sample data manipulations.
    """
    def __init__(self) -> None:
        """
        Initializes the UtilityService, loads a sample dataset, and sets up required modules.
        """
        logger.info("Initializing Utility Service.")
        self.sampleDataset = sns.load_dataset("tips")
        self.imageToInsightsModule = ImageToInsights()
        self.insightContextBuilder = InsightContextBuilder()
        self.signalEngine = SignalEngine()
        self.speechToTextModule = SpeechToText()
        self.client = client
    
    def getSpeechTranscript(self, speechToText: SpeechToTextModel) -> str:
        """
        Converts base64-encoded audio to text using the SpeechToText module.

        Args:
            speechToText (SpeechToTextModel): Model containing the base64-encoded audio string.
        Returns:
            str: The transcribed text from the audio.
        Raises:
            CustomException: If transcription fails.
        """
        try:
            transcriptText = self.speechToTextModule.getTranscript(b64String = speechToText.b64String)
            return transcriptText
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
    def getInsightsFromImage(self, imageToInsights: ImageToInsightsModel) -> dict:
        """
        Extracts structured, evidence-backed insights from a base64-encoded dashboard image
        using the hybrid data + statistics + domain + LLM pipeline.

        Orchestrates context building, statistical signal extraction, LLM inference,
        and persistence of the generated insights.

        Args:
            imageToInsights (ImageToInsightsModel): Model containing image, project context,
                and user objectives.

        Returns:
            dict: Structured insights with diagnostic_insights, prescriptive_actions, missing_data.

        Raises:
            CustomException: If insight generation fails.
        """
        try:
            context = self.insightContextBuilder.buildContext(
                projectId=imageToInsights.projectId,
                pageId=imageToInsights.pageId,
                widgetIds=imageToInsights.widgetIds,
                filters=imageToInsights.filters,
                dateRange=imageToInsights.dateRange,
                businessGoals=imageToInsights.businessGoals,
                decisionFocus=imageToInsights.decisionFocus,
                audience=imageToInsights.audience,
            )

            statisticalSummary = self.signalEngine.buildStatisticalSummary(
                chartData=context.get("chartData", [])
            )
            context["statisticalSummary"] = statisticalSummary

            insights = self.imageToInsightsModule.getInsights(
                b64String=imageToInsights.b64String,
                context=context,
            )

            self._persistDashboardInsight(
                projectId=imageToInsights.projectId,
                pageId=imageToInsights.pageId,
                audience=imageToInsights.audience,
                insights=insights,
            )

            return insights
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def _persistDashboardInsight(self, projectId: str, pageId: str | None, audience: str, insights: dict) -> None:
        """
        Persists a generated insight record to dashboardInsights.json in project storage.

        Args:
            projectId (str): The project identifier.
            pageId (str | None): The dashboard page the insight was generated for.
            audience (str): The target audience of the insight.
            insights (dict): The structured insight payload.
        """
        try:
            record = {
                "id": str(uuid.uuid4()),
                "pageId": pageId,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "audience": audience,
                "insights": insights,
                "status": "new",
            }

            existing = self._loadDashboardInsightsFile(projectId)
            existing.append(record)

            with io.BytesIO() as buffer:
                buffer.write(json.dumps(existing, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(
                    path=f"{projectId}/dashboardInsights.json",
                    file=buffer.getvalue(),
                    file_options={"upsert": "true"},
                )
            logger.info(f"Dashboard insight persisted for project {projectId}.")
        except Exception as e:
            logger.warning(f"Failed to persist dashboard insight: {e}")

    def _loadDashboardInsightsFile(self, projectId: str) -> list:
        """
        Loads dashboardInsights.json from Supabase storage.

        Returns:
            list: Existing insight records, or empty list if file does not exist.
        """
        try:
            files = self.client.storage.from_("AnalyticsHub").list(path=projectId)
            if "dashboardInsights.json" not in [x.get("name") for x in files]:
                return []
            fileUrl = os.environ["FILE_URL"].format(projectId=projectId, fileName="dashboardInsights.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            data = json.loads(urlopen(fileUrl).read())
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def getDashboardInsights(self, projectId: str) -> list:
        """
        Retrieves all persisted dashboard insights for a project.

        Args:
            projectId (str): The project identifier.

        Returns:
            list: List of insight records.

        Raises:
            CustomException: If retrieval fails.
        """
        try:
            return self._loadDashboardInsightsFile(projectId)
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def updateDashboardInsightStatus(self, projectId: str, insightId: str, status: str) -> dict:
        """
        Updates the lifecycle status of a persisted dashboard insight.

        Args:
            projectId (str): The project identifier.
            insightId (str): The unique insight record ID.
            status (str): New status — one of "new", "accepted", "rejected", "implemented".

        Returns:
            dict: The updated insight record.

        Raises:
            CustomException: If the update fails or the insight is not found.
        """
        try:
            if status not in VALID_INSIGHT_STATUSES:
                raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_INSIGHT_STATUSES}.")

            records = self._loadDashboardInsightsFile(projectId)
            updatedRecord = None
            for record in records:
                if record.get("id") == insightId:
                    record["status"] = status
                    updatedRecord = record
                    break

            if updatedRecord is None:
                raise ValueError(f"Insight record '{insightId}' not found in project '{projectId}'.")

            with io.BytesIO() as buffer:
                buffer.write(json.dumps(records, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(
                    path=f"{projectId}/dashboardInsights.json",
                    file=buffer.getvalue(),
                    file_options={"upsert": "true"},
                )

            logger.info(f"Dashboard insight {insightId} status updated to '{status}'.")
            return updatedRecord
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def sendForecasts(self) -> AsyncResult:
        """
        Sends a task to generate forecasts asynchronously using Celery.

        Returns:
            AsyncResult: The result object for the Celery task.
        Raises:
            CustomException: If the task submission fails.
        """
        try:
            return celeryApp.send_task("AnalyticsHub.generateForecasts")
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception     
        
    def tempFunc(self, num: int) -> str:
        """
        Returns different representations or aggregations of the sample dataset based on the input parameter.

        Args:
            num (int): Determines the type of data transformation to perform.
                1: Returns the dataset as a list of records.
                2: Returns a pivot table by day and sex.
                3: Returns a pivot table by day, sex, and time.
                Other: Returns a pivot table by day, sex, time, and includes tip.
        Returns:
            str: The resulting data as a JSON string or list of records.
        Raises:
            CustomException: If data processing fails.
        """
        try:
            if num == 1:
                result = self.sampleDataset.to_dict(orient = "records")
            elif num == 2:
                result = pd.pivot_table(self.sampleDataset, index="day", columns=["sex"], aggfunc="count", values=["total_bill"]).to_json()
            elif num == 3:
                result = pd.pivot_table(self.sampleDataset, index="day", columns=["sex", "time"], aggfunc="count", values=["total_bill"]).to_json()
            else:
                result = pd.pivot_table(self.sampleDataset, index="day", columns=["sex", "time"], aggfunc="count", values=["total_bill", "tip"]).to_json()
        except Exception as e:      
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
utilityService = UtilityService()
