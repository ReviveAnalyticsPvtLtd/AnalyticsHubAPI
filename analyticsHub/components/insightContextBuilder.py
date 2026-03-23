"""
insightContextBuilder.py

Assembles a rich context payload for the hybrid image-to-insights pipeline.
Loads project metadata, dashboard configuration, domain expert profiles,
and user-provided objectives into a single dict consumed by the LLM prompt.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["InsightContextBuilder"]


from utils.exceptionHandler import CustomException
from urllib.request import urlopen
from utils.logger import logger
from api.commons import client
from dataclasses import dataclass
import json
import time
import os


@dataclass
class InsightContextBuilderConfig:
    """
    Configuration dataclass for InsightContextBuilder.

    Attributes:
        storageBucket (str): Supabase storage bucket name for project artifacts.
        domainBucket (str): Supabase storage bucket name for domain-specific KPI profiles.
    """
    storageBucket: str = "AnalyticsHub"
    domainBucket: str = "DomainSpecificKpis"


class InsightContextBuilder:
    """
    Builds a consolidated context dict from project artifacts and user-supplied
    parameters for use in the hybrid insight generation pipeline.
    """
    def __init__(self):
        """Initializes the InsightContextBuilder and sets up the Supabase client."""
        logger.info("Initializing InsightContextBuilder.")
        self.config = InsightContextBuilderConfig()
        self.client = client

    def _loadJsonFromStorage(self, projectId: str, fileName: str) -> dict | list | None:
        """
        Loads a JSON file from Supabase storage with cache-busting.

        Returns None if the file does not exist in the project folder.
        """
        try:
            files = self.client.storage.from_(self.config.storageBucket).list(path=projectId)
            if fileName not in [x.get("name") for x in files]:
                return None
            fileUrl = os.environ["FILE_URL"].format(projectId=projectId, fileName=fileName).replace(".parquet", "") + f"?cb={int(time.time())}"
            return json.loads(urlopen(fileUrl).read())
        except Exception:
            return None

    def _loadDomainContext(self, projectId: str) -> dict:
        """
        Loads the domain expert profile for a project from the DomainSpecificKpis bucket.

        Includes extended playbook keys when available: northStar, driverTree,
        decisionLevers, alertThresholds, recommendedActions.
        """
        try:
            row = self.client.table("Projects").select("domainExpert").eq("projectId", projectId).execute().data
            if not row:
                return {}
            domainFile = row[0].get("domainExpert", "") + ".json"
            available = [x.get("name") for x in self.client.storage.from_(self.config.domainBucket).list()]
            if domainFile not in available:
                return {}
            domainFileUrl = os.environ["DOMAIN_FILE_URL"].format(fileName=domainFile) + f"?cb={int(time.time())}"
            domainData = json.loads(urlopen(domainFileUrl).read())
            playbookKeys = ["northStar", "driverTree", "decisionLevers", "alertThresholds", "recommendedActions"]
            playbook = {k: domainData[k] for k in playbookKeys if k in domainData}
            return {
                "profile": domainData,
                "playbook": playbook
            }
        except Exception:
            return {}

    def _extractChartData(self, dashboardConfig: dict, pageId: str | None, widgetIds: list[str] | None) -> list[dict]:
        """
        Extracts compact chart data from dashboard widgets, filtered by page and widget IDs.
        """
        chartData = []
        if not dashboardConfig:
            return chartData

        pages = [pageId] if pageId else list(dashboardConfig.keys())
        for pid in pages:
            page = dashboardConfig.get(pid)
            if not page:
                continue
            for widget in page.get("widgets", []):
                if widgetIds and widget.get("id") not in widgetIds:
                    continue
                chartData.append({
                    "widgetId": widget.get("id"),
                    "title": widget.get("title"),
                    "chartType": widget.get("chartType"),
                    "data": widget.get("data"),
                    "xLabels": widget.get("xLabels"),
                    "yLabels": widget.get("yLabels"),
                    "label": widget.get("label"),
                })
        return chartData

    def buildContext(
        self,
        projectId: str,
        pageId: str | None = None,
        widgetIds: list[str] | None = None,
        filters: list[dict] | None = None,
        dateRange: dict | None = None,
        businessGoals: list[str] | None = None,
        decisionFocus: str | None = None,
        audience: str = "owner",
    ) -> dict:
        """
        Assembles the full context payload for insight generation.

        Args:
            projectId (str): The project identifier.
            pageId (str | None): Optional dashboard page to focus on.
            widgetIds (list[str] | None): Optional widget IDs to restrict chart data.
            filters (list[dict] | None): Active dashboard filters.
            dateRange (dict | None): Active date range selection.
            businessGoals (list[str] | None): User-provided business objectives.
            decisionFocus (str | None): Decision focus area (growth, cost, risk, retention, efficiency).
            audience (str): Target audience — "owner" or "analyst".

        Returns:
            dict: Context payload with keys: metadata, dashboard, chartData, domainContext, objectiveContext.

        Raises:
            CustomException: If a critical error prevents context assembly.
        """
        try:
            logger.info(f"Building insight context for project {projectId}.")

            metadata = self._loadJsonFromStorage(projectId, "metadata.json") or {}
            dashboardConfig = self._loadJsonFromStorage(projectId, "dashboardConfig.json") or {}
            existingInsights = self._loadJsonFromStorage(projectId, "insights.json") or {}
            domainContext = self._loadDomainContext(projectId)
            chartData = self._extractChartData(dashboardConfig, pageId, widgetIds)

            pageName = None
            if pageId and dashboardConfig.get(pageId):
                pageName = dashboardConfig[pageId].get("name")

            context = {
                "metadata": metadata,
                "dashboard": {
                    "pageId": pageId,
                    "pageName": pageName,
                    "widgetCount": len(chartData),
                    "existingInsights": existingInsights,
                },
                "chartData": chartData,
                "domainContext": domainContext,
                "objectiveContext": {
                    "audience": audience,
                    "businessGoals": businessGoals or [],
                    "decisionFocus": decisionFocus,
                    "filters": filters or [],
                    "dateRange": dateRange,
                },
            }

            logger.info(f"Insight context assembled: {len(chartData)} widgets, domain={'present' if domainContext else 'absent'}.")
            return context
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
