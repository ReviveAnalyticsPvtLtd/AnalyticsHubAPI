"""
reportingService module provides services for generating charts and panel charts using reporting workflows and code templates.

Author: Rauhan Ahmed Siddiqui
Version: 1.0.0
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["reportingService"] 


from analyticsHub.workflows.reportingToolWorkflow import reportingToolWorkflow
from api.models import GenerateChartInput, PanelChartDetails
from utils.exceptionHandler import CustomException
from utils.codeExecutor import replManager
from analyticsHub.utils import readYaml
from urllib.request import urlopen
from utils.logger import logger
from api.commons import client
from string import Template
import orjson
import json
import time
import os

class ReportingService:
    """
    Service class for generating charts and panel charts for reporting purposes.
    """
    def __init__(self) -> None:
        """
        Initializes the ReportingService, loads code templates, and sets up the reporting workflow.
        """
        logger.info("Initializing Reporting Service.")
        self.codeTemplates = readYaml(os.path.join(os.getcwd(), "codeTemplates.yaml"))
        self.reportingToolWorkflow = reportingToolWorkflow
        self.client = client

    def generateChart(self, chartDetails: GenerateChartInput) -> dict:
        """
        Generates a chart based on the provided chart details using the reporting workflow.

        Args:
            chartDetails (GenerateChartInput): Input details for generating the chart.
        Returns:
            dict: The generated chart data.
        Raises:
            CustomException: If chart generation fails.
        """
        try:
            fileUrl = os.environ["FILE_URL"].format(projectId = chartDetails.projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            response = self.reportingToolWorkflow.invoke({
                "metadata": json.loads(urlopen(fileUrl).read()),
                "inputQuery": chartDetails.inputQuery,
                "projectId": chartDetails.projectId
            })
            return response
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
    def generatePanelChart(self, panelChartDetails: PanelChartDetails) -> dict:
        """
        Generates a panel chart, optionally using blend configuration if available.

        Args:
            panelChartDetails (PanelChartDetails): Input details for generating the panel chart.
        Returns:
            dict: The generated panel chart data, including generated code.
        Raises:
            CustomException: If panel chart generation fails.
        """
        try:
            allFiles = [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = panelChartDetails.projectId)]
            if "".join([panelChartDetails.dataSource, ".parquet"]) in allFiles:
                response = replManager.run(f"getDataForChart(projectId='{panelChartDetails.projectId}', chartType='{panelChartDetails.chartType}', xAxis='{panelChartDetails.xAxis}', yAxis='{panelChartDetails.yAxis}', aggregationMetric='{panelChartDetails.aggregationMetric}', tablesUsed='{panelChartDetails.dataSource}')")    
                generatedCodeTemplate = Template(self.codeTemplates.get("panelChartWithoutBlend"))
                generatedCode = generatedCodeTemplate.substitute(
                    projectId = panelChartDetails.projectId,
                    chartType = panelChartDetails.chartType,
                    xAxis = panelChartDetails.xAxis,
                    yAxis = panelChartDetails.yAxis,
                    aggregationMetric = panelChartDetails.aggregationMetric,
                    tablesUsed = panelChartDetails.dataSource
                )
            elif "blendConfig.json" in allFiles:
                blendConfigUrl = os.environ["FILE_URL"].format(projectId = panelChartDetails.projectId, fileName = "blendConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                blendConfig = orjson.loads(urlopen(blendConfigUrl).read())
                tablesUsed = blendConfig[panelChartDetails.dataSource].get("tables")
                joinTypes = blendConfig[panelChartDetails.dataSource].get("joinTypes")
                blendOn = blendConfig[panelChartDetails.dataSource].get("blendOn")
                response = replManager.run(f"getDataForChart(projectId='{panelChartDetails.projectId}', chartType='{panelChartDetails.chartType}', xAxis='{panelChartDetails.xAxis}', yAxis='{panelChartDetails.yAxis}', aggregationMetric='{panelChartDetails.aggregationMetric}', tablesUsed={tablesUsed}, joinTypes={joinTypes}, blendOn={blendOn})")
                generatedCodeTemplate = Template(self.codeTemplates.get("panelChartWithBlend"))
                generatedCode = generatedCodeTemplate.substitute(
                    projectId = panelChartDetails.projectId,
                    chartType = panelChartDetails.chartType,
                    xAxis = panelChartDetails.xAxis,
                    yAxis = panelChartDetails.yAxis,
                    aggregationMetric = panelChartDetails.aggregationMetric,
                    tablesUsed = tablesUsed,
                    joinTypes = joinTypes,
                    blendOn = blendOn
                )
            else:
                pass
            response = orjson.loads(response.encode("utf-8"))
            response.update({"generatedCode": generatedCode})
            return response
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
    
reportingService = ReportingService()