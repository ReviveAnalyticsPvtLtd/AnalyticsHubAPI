"""
reportingService module provides services for generating charts and panel charts using reporting workflows and code templates.

This module defines the ReportingService class, which offers methods to generate single and panel charts for reporting purposes. It leverages workflows, code templates, and data blending to produce various chart types based on user input.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["reportingService"] 


from api.models import GenerateChartInput, PanelChartDetails, GenerateChartsInParallel
from analyticsHub.workflows.reportingToolWorkflow import buildReportingWorkflow
from utils.exceptionHandler import CustomException
from concurrent.futures import ProcessPoolExecutor
from utils.initMethods import fetch_data
from analyticsHub.utils import readYaml
from urllib.request import urlopen
from utils.logger import logger
from api.commons import client
from string import Template
import pandas as pd
import orjson
import json
import time
import os

WORKFLOW = None
def initWorkflow():
    global WORKFLOW
    logger.disable("") 
    try:
        WORKFLOW = buildReportingWorkflow()
    finally:
        logger.enable("") 

class ReportingService:
    """
    Service class for generating charts and panel charts for reporting purposes.

    This class provides methods to generate single charts and panel (multi-chart) visualizations. It supports data blending, aggregation, and uses code templates to generate code snippets for reproducibility.
    """
    def __init__(self) -> None:
        """
        Initializes the ReportingService.

        Loads code templates from YAML, sets up the reporting workflow, and initializes the storage client.
        """
        logger.info("Initializing Reporting Service.")
        self.codeTemplates = readYaml(os.path.join(os.getcwd(), "codeTemplates.yaml"))
        self.reportingToolWorkflow = buildReportingWorkflow()
        self.client = client

    @staticmethod
    def _generatePanelChart(projectId: str, chartType: str, xAxis: str, yAxis: str, aggregationMetric: str | None, dataSourceName: str, tablesUsed: list[str] | str, joinTypes: list[str] | None = None, blendOn: list[str] | None = None, **kwargs) -> dict:
        """
        Prepares and aggregates data for charting based on the specified parameters.

        Args:
            projectId (str): The project ID.
            chartType (str): The type of chart to generate (e.g., bar, line, pie, table, pivot, etc.).
            xAxis (str): The column to use for the X axis.
            yAxis (str): The column to use for the Y axis.
            aggregationMetric (str, optional): The aggregation metric (sum, mean, etc.).
            dataSourceName (str): The name of the data source.
            tablesUsed (list[str] | str): Tables to use for the chart. Can be a single table or a list for blending.
            joinTypes (list[str], optional): Join types for merging tables (if blending).
            blendOn (list[str], optional): Columns to join on (if blending).
            **kwargs: Additional keyword arguments for pivot charts (index, columns, values, selectedColumns).

        Returns:
            dict: Chart-ready data structure suitable for frontend rendering.
        """
        if isinstance(tablesUsed, list):
            allTables = [fetch_data(projectId, x) for x in tablesUsed]
            result = allTables[0]
            for i in range(len(joinTypes)):
                result = pd.merge(left = result, right = allTables[i+1], on = blendOn[i], how = joinTypes[i], suffixes = ['_left', '_right'])
        else:
            result = fetch_data(projectId, tablesUsed)
        if chartType != "pivot":
            if aggregationMetric == "sum":
                finalResult = result.groupby(xAxis)[yAxis].sum().reset_index()
            elif aggregationMetric == "mean":
                finalResult = result.groupby(xAxis)[yAxis].mean().reset_index()
            elif aggregationMetric == "median":
                finalResult = result.groupby(xAxis)[yAxis].median().reset_index()
            elif aggregationMetric == "max":
                finalResult = result.groupby(xAxis)[yAxis].max().reset_index()
            elif aggregationMetric == "min":
                finalResult = result.groupby(xAxis)[yAxis].min().reset_index()
            elif aggregationMetric == "count":
                finalResult = result.groupby(xAxis)[yAxis].count().reset_index()
            elif aggregationMetric == "std":
                finalResult = result.groupby(xAxis)[yAxis].std().reset_index()
            elif aggregationMetric == "var":
                finalResult = result.groupby(xAxis)[yAxis].var().reset_index()
            else:
                finalResult = result
        else:
            finalResult = result
        if chartType in ["bar", "line", "radar", "polarArea"]:
            response = {
                "chartType": chartType,
                "title": f"{chartType.capitalize()} Chart of {xAxis} vs {yAxis}",
                "xLabels": xAxis,
                "yLabels": yAxis,
                "data": {
                    "labels": finalResult[xAxis].tolist(),
                    "datasets": [
                        {
                            "label": f"{aggregationMetric} of {yAxis}",
                            "data": finalResult[yAxis].tolist()
                        }
                    ]
                }
            }
        elif chartType in ["pie", "doughnut"]:
            response = {
                "chartType": chartType,
                "title": f"{chartType.capitalize()} Chart of {xAxis} vs {yAxis}",
                "data": {
                    "labels": finalResult[xAxis].tolist(),
                    "datasets": [
                        {
                            "label": f"{aggregationMetric} of {yAxis}",
                            "data": finalResult[yAxis].tolist()
                        }
                    ]
                }
            }
        elif chartType == "scatter":
            response = {
                "chartType": chartType,
                "title": f"{chartType.capitalize()} Chart of {xAxis} vs {yAxis}",
                "xLabels": xAxis,
                "yLabels": yAxis,
                "data": {
                    "datasets": [
                        {
                            "label": f"{aggregationMetric} of {yAxis}",
                            "data": [
                                {"x": row[xAxis], "y": row[yAxis]} for _, row in finalResult.iterrows()
                            ]
                        }
                    ]
                }
            }
        elif chartType == "card":
            if len(finalResult) > 0:
                single_value = finalResult[yAxis].iloc[0]
                response = {
                    "chartType": "card",
                    "title": f"{chartType.capitalize()} Chart of {xAxis} vs {yAxis}",
                    "label": f"{aggregationMetric} of {yAxis}",
                    "data": single_value
                }
            else:
                response = {
                    "chartType": "card",
                    "title": f"{chartType.capitalize()} Chart of {xAxis} vs {yAxis}",
                    "label": f"{aggregationMetric} of {yAxis}",
                    "data": 0
                }
        elif chartType == "table":
            if kwargs.get("selectedColumns") is None:
                selectedColumns = finalResult.columns.tolist()
            else:
                selectedColumns = kwargs.get("selectedColumns")
            response = {
                "chartType": "table",
                "title": f"{dataSourceName} Data",
                "data": finalResult[selectedColumns].to_dict(orient="records")
            }
        elif chartType == "pivot":
            pivotData = pd.pivot_table(finalResult, index=kwargs.get("index"), columns=kwargs.get("columns"), aggfunc=aggregationMetric, values=kwargs.get("values")).to_json()
            response = {
                "chartType": "pivot",
                "title": f"Pivot for {dataSourceName}",
                "data": orjson.loads(pivotData)
            }
        return response
    
    def generateChart(self, chartDetails: GenerateChartInput) -> dict:
        """
        Generates a chart based on the provided chart details using the reporting workflow.

        Args:
            chartDetails (GenerateChartInput): Input details for generating the chart, including query, project ID, and chart configuration.

        Returns:
            dict: The generated chart data as a dictionary.

        Raises:
            CustomException: If chart generation fails for any reason.
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

    @staticmethod
    def _generateSingleChartForParallel(metadata: dict, projectId: str, query: str) -> dict:
        """
        Helper function to generate a single chart in parallel.

        Args:
            workflow: The reporting tool workflow instance.
            metadata: The metadata for the project.
            projectId: The project ID.
            query: The input query for the chart.

        Returns:
            dict: The generated chart data.

        Raises:
            CustomException: If chart generation fails for any reason.
        """
        try:
            global WORKFLOW
            response = WORKFLOW.invoke({
                "metadata": metadata,
                "inputQuery": query,
                "projectId": projectId
            })
            _ = response.pop("metadata")
            _ = response.pop("rephrasedQuery")
            _ = response.pop("codeOutput")
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def generateChartsInParallel(self, details: GenerateChartsInParallel) -> list[dict]:
        """
        Generates multiple charts in parallel based on the provided details. 

        Args:
            details (GenerateChartsInParallel): The details for generating charts in parallel, including project ID and input queries.

        Returns:
            list[dict]: A list of generated chart data dictionaries.

        Raises:
            CustomException: If chart generation fails for any reason.
        """
        try:
            fileUrl = os.environ["FILE_URL"].format(projectId=details.projectId, fileName="metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            metadata = json.loads(urlopen(fileUrl).read())
            with ProcessPoolExecutor(max_workers=4, initializer=initWorkflow) as executor:
                futures = [
                    executor.submit(self._generateSingleChartForParallel, metadata, details.projectId, query)
                    for query in details.inputQueries
                ]
                responses = [f.result() for f in futures]
            return responses
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def generatePanelChart(self, panelChartDetails: PanelChartDetails) -> dict:
        """
        Generates a panel chart, optionally using blend configuration if available.

        This method checks for the presence of a data source or blend configuration, prepares the data, and generates the panel chart. It also generates the code used for chart creation using code templates.

        Args:
            panelChartDetails (PanelChartDetails): Input details for generating the panel chart, including project ID, chart type, axes, aggregation, and blend info.

        Returns:
            dict: The generated panel chart data, including the generated code snippet.

        Raises:
            CustomException: If panel chart generation fails for any reason.
        """
        try:
            allFiles = [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = panelChartDetails.projectId)]
            if "".join([panelChartDetails.dataSource, ".parquet"]) in allFiles:
                response = self._generatePanelChart(
                    projectId=panelChartDetails.projectId,
                    chartType=panelChartDetails.chartType,
                    xAxis=panelChartDetails.xAxis,
                    yAxis=panelChartDetails.yAxis,
                    aggregationMetric=panelChartDetails.aggregationMetric,
                    dataSourceName=panelChartDetails.dataSource,
                    tablesUsed=panelChartDetails.dataSource,
                    index=panelChartDetails.index,
                    columns=panelChartDetails.columns,
                    values=panelChartDetails.values,
                    selectedColumns=panelChartDetails.selectedColumns
                )
                generatedCodeTemplate = Template(self.codeTemplates.get("panelChartWithoutBlend"))
                generatedCode = generatedCodeTemplate.substitute(
                    projectId = panelChartDetails.projectId,
                    chartType = panelChartDetails.chartType,
                    xAxis = panelChartDetails.xAxis,
                    yAxis = panelChartDetails.yAxis,
                    aggregationMetric = panelChartDetails.aggregationMetric,
                    dataSourceName = panelChartDetails.dataSource,
                    tablesUsed = panelChartDetails.dataSource,
                    index=panelChartDetails.index,
                    columns=panelChartDetails.columns,
                    values=panelChartDetails.values,
                    selectedColumns=panelChartDetails.selectedColumns
                )
            elif "blendConfig.json" in allFiles:
                blendConfigUrl = os.environ["FILE_URL"].format(projectId = panelChartDetails.projectId, fileName = "blendConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                blendConfig = orjson.loads(urlopen(blendConfigUrl).read())
                tablesUsed = blendConfig[panelChartDetails.dataSource].get("tables")
                joinTypes = blendConfig[panelChartDetails.dataSource].get("joinTypes")
                blendOn = blendConfig[panelChartDetails.dataSource].get("blendOn")
                response = self._generatePanelChart(
                    projectId=panelChartDetails.projectId, 
                    chartType=panelChartDetails.chartType, 
                    xAxis=panelChartDetails.xAxis, 
                    yAxis=panelChartDetails.yAxis, 
                    aggregationMetric=panelChartDetails.aggregationMetric, 
                    dataSourceName=panelChartDetails.dataSource,
                    tablesUsed=tablesUsed, 
                    joinTypes=joinTypes, 
                    blendOn=blendOn,
                    index=panelChartDetails.index,
                    columns=panelChartDetails.columns,
                    values=panelChartDetails.values,
                    selectedColumns=panelChartDetails.selectedColumns
                )
                generatedCodeTemplate = Template(self.codeTemplates.get("panelChartWithBlend"))
                generatedCode = generatedCodeTemplate.substitute(
                    projectId = panelChartDetails.projectId,
                    chartType = panelChartDetails.chartType,
                    xAxis = panelChartDetails.xAxis,
                    yAxis = panelChartDetails.yAxis,
                    aggregationMetric = panelChartDetails.aggregationMetric,
                    dataSourceName = panelChartDetails.dataSource,
                    tablesUsed = tablesUsed,
                    joinTypes = joinTypes,
                    blendOn = blendOn,
                    index=panelChartDetails.index,
                    columns=panelChartDetails.columns,
                    values=panelChartDetails.values,
                    selectedColumns=panelChartDetails.selectedColumns
                )
            else:
                pass
            response.update({"generatedCode": generatedCode})
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
    
reportingService = ReportingService()