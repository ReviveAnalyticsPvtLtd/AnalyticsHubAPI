"""
reportingService module provides services for generating charts and panel charts using reporting workflows and code templates.

This module defines the ReportingService class, which offers methods to generate single and panel charts for reporting purposes. It leverages workflows, code templates, and data blending to produce various chart types based on user input.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["reportingService"] 


from api.models import GenerateChartInput, PanelChartDetails, GenerateChartsInParallel, SaveQuery, DeleteQuery
from nubrix.components.dashboardNameGenerator import DashboardNameGenerator
from nubrix.workflows.reportingToolWorkflow import buildReportingWorkflow
from api.commons import updateProjectModifiedAt
from utils.exceptionHandler import CustomException
from concurrent.futures import ThreadPoolExecutor
from utils.initMethods import fetch_data
from nubrix.utils import readYaml
from urllib.request import urlopen
from utils.logger import logger
from api.commons import client
import pandas as pd
import string
import orjson
import random
import json
import threading
import uuid
import time
import os
import io

threadLocal = threading.local()

def initWorkflow():
    logger.disable("") 
    try:
        threadLocal.workflow = buildReportingWorkflow()
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
            chartType (str): The type of chart to generate (e.g., bar, line, pie, table, pivot, geoMap, etc.).
            xAxis (str): The column to use for the X axis.
            yAxis (str): The column to use for the Y axis.
            aggregationMetric (str, optional): The aggregation metric (sum, mean, etc.).
            dataSourceName (str): The name of the data source.
            tablesUsed (list[str] | str): Tables to use for the chart. Can be a single table or a list for blending.
            joinTypes (list[str], optional): Join types for merging tables (if blending).
            blendOn (list[str], optional): Columns to join on (if blending).
            **kwargs: Additional keyword arguments for few charts (index, columns, values, selectedColumns, mapType, isFilterApplied, filters).

        Returns:
            dict: Chart-ready data structure suitable for frontend rendering.
        """
        filters = kwargs.get("filters")
        hasFilters = filters and len(filters) > 0
        
        if isinstance(tablesUsed, list):
            allTables = [fetch_data(projectId, x) for x in tablesUsed]
            result = allTables[0]
            for i in range(len(joinTypes)):
                result = pd.merge(left = result, right = allTables[i+1], on = blendOn[i], how = joinTypes[i], suffixes = ['_left', '_right'])
        else:
            result = fetch_data(projectId, tablesUsed)

        if hasFilters:
            for filter_item in filters:
                for column_path, condition in filter_item.items():
                    column = column_path.split(".")[-1]
                    if column not in result.columns:
                        continue
                    if isinstance(condition, dict):
                        if result[column].dtype == "object":
                            if "contains" in condition:
                                result = result[result[column].str.contains(condition["contains"], case=False, na=False)]
                            if "startswith" in condition:
                                result = result[result[column].str.startswith(condition["startswith"], na=False)]
                            if "endswith" in condition:
                                result = result[result[column].str.endswith(condition["endswith"], na=False)]
                        else:
                            if "min" in condition:
                                result = result[result[column] >= condition["min"]]
                            if "max" in condition:
                                result = result[result[column] <= condition["max"]]
                    elif isinstance(condition, (list, tuple, set)):
                        result = result[result[column].isin(condition)]
                    else:
                        result = result[result[column] == condition]
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
        elif chartType == "geoMap":
            geoCodeCol = kwargs.get("geoCodeColumn")
            points = []
            hasGeoCode = geoCodeCol and geoCodeCol not in ["None", ""]

            geocode_cache = {}

            if hasGeoCode:
                from geopy.geocoders import Nominatim
                from geopy.extra.rate_limiter import RateLimiter
                import math
                geolocator = Nominatim(user_agent="NubrixAI", timeout=10)
                rate_limited_geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)

            def geocode_with_cache(value, geocode_func):
                if value in geocode_cache:
                    return geocode_cache[value]
                try:
                    res = geocode_func(value)
                    geocode_cache[value] = res
                except Exception:
                    geocode_cache[value] = None
                return geocode_cache[value]

            for _, row in finalResult.iterrows():
                lat = None
                lon = None

                if xAxis in finalResult.columns and yAxis in finalResult.columns:
                    latEx = row[xAxis]
                    lonEx = row[yAxis]
                    if pd.notna(latEx) and pd.notna(lonEx):
                        lat = latEx
                        lon = lonEx

                if (lat is None or lon is None) and hasGeoCode:
                    g = str(row.get(geoCodeCol, ""))
                    if g and g != "nan":
                        res = geocode_with_cache(g, rate_limited_geocode)
                        if res:
                            latC = res.latitude
                            lonC = res.longitude
                            if pd.notna(latC) and pd.notna(lonC):
                                lat = latC
                                lon = lonC

                if lat is not None and lon is not None:
                    points.append({
                        "id": "".join(random.choice(string.ascii_letters + string.digits) for i in range(16)),
                        "lat": lat,
                        "long": lon
                    })

            response = {
                "chartType": "geoMap",
                "map": {
                    "mapType": "scatterMap",
                    "data": {
                        "points": points
                    }
                }
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
            updateProjectModifiedAt(chartDetails.projectId)
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
            workflow = threadLocal.workflow
            response = workflow.invoke({
                "metadata": metadata,
                "inputQuery": query,
                "projectId": projectId
            })
            _ = response.pop("metadata", None)
            _ = response.pop("rephrasedQuery", None)
            _ = response.pop("codeOutput", None)
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def generateChartsInParallel(self, details: GenerateChartsInParallel) -> dict:
        """
        Generates multiple charts in parallel based on the provided details and export them to an automatic dashboard page. 

        Args:
            details (GenerateChartsInParallel): The details for generating charts in parallel, including project ID and input queries.

        Returns:
            dict: A dictionary containing the generated chart data.

        Raises:
            CustomException: If chart generation fails for any reason.
        """
        try:
            # Generating charts in parallel
            metadataUrl = os.environ["FILE_URL"].format(projectId=details.projectId, fileName="metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            insightsUrl = os.environ["FILE_URL"].format(projectId=details.projectId, fileName="insights.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            metadata = json.loads(urlopen(metadataUrl).read())
            insights = json.loads(urlopen(insightsUrl).read())
            
            # Remove any potential duplicate queries while preserving order
            uniqueQueries = list(dict.fromkeys(details.inputQueries))

            with ThreadPoolExecutor(max_workers=6, initializer=initWorkflow) as executor:
                futures = [
                    executor.submit(self._generateSingleChartForParallel, metadata, details.projectId, query)
                    for query in uniqueQueries
                ]
                responses = [f.result() for f in futures]

            # Check if there is already an existing dashboard configuration
            file_exists = "dashboardConfig.json" in [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = details.projectId)]
            dashboardConfig = {}
            if file_exists:
                fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                dashboardConfig = json.loads(urlopen(fileUrl).read())

            # Find the existing automatic page
            pageId = None
            for pid, pdata in dashboardConfig.items():
                if pdata.get("isAutomatic"):
                    pageId = pid
                    break

            # Fallback for old automatic dashboard which didn't have the isAutomatic flag
            if not pageId and dashboardConfig:
                pageId = list(dashboardConfig.keys())[0]

            if not pageId:
                # Generate a dynamic dashboard name only if creating a new one
                dashboardNameChain = DashboardNameGenerator().getDashboardNameGeneratorChain()
                dashboardName = dashboardNameChain.invoke({
                    "queries": "\n".join(uniqueQueries),
                    "metadata": json.dumps(metadata)
                }).strip()

                # Create a new dashboard page
                pageId = str(uuid.uuid4())
                dashboardConfig[pageId] = {"name": dashboardName, "isAutomatic": True, "widgets": []}

            # Export to dashboard
            pageDict = dashboardConfig.get(pageId)
            
            existingWidgets = pageDict.get("widgets", [])
            newWidgets = []
            
            for widget in responses:
                widgetId = str(uuid.uuid4())
                chartType = widget.get("finalOutput", {}).get("chartType")
                data = widget.get("finalOutput", {}).get("data")
                if chartType == "card":
                    if isinstance(data, (int, float)):
                        data = float(f"{data:.2f}")
                    newWidgets.append({
                        "id": widgetId,
                        "chartType": chartType,
                        "title": widget.get("finalOutput", {}).get("title"),
                        "label": widget.get("finalOutput", {}).get("label"),
                        "xLabels": widget.get("finalOutput", {}).get("xLabels"),
                        "yLabels": widget.get("finalOutput", {}).get("yLabels"),
                        "data": data,
                        "layout": {"x": 0, "y": 0, "w": 4, "h": 6},
                        "generatedCode": widget.get("generatedCode")
                    })
                else:
                    newWidgets.append({
                        "id": widgetId,
                        "chartType": chartType,
                        "title": widget.get("finalOutput", {}).get("title"),
                        "label": widget.get("finalOutput", {}).get("label"),
                        "xLabels": widget.get("finalOutput", {}).get("xLabels"),
                        "yLabels": widget.get("finalOutput", {}).get("yLabels"),
                        "data": data,
                        "layout": {"x": 0, "y": 0, "w": 6, "h": 10},
                        "generatedCode": widget.get("generatedCode")
                    })
            
            allWidgets = existingWidgets + newWidgets
            cards = [w for w in allWidgets if w.get("chartType") == "card"]
            otherWidgets = [w for w in allWidgets if w.get("chartType") != "card"]
            
            current_y = 0
            current_x = 0
            
            for card in cards:
                card["layout"] = {
                    "x": current_x,
                    "y": current_y,
                    "w": 4,
                    "h": 6
                }
                current_x += 4
                if current_x >= 12:
                    current_x = 0
                    current_y += 6
            
            if current_x > 0:
                current_y += 6
                current_x = 0
                
            for widget in otherWidgets:
                widget["layout"] = {
                    "x": current_x,
                    "y": current_y,
                    "w": 6,
                    "h": 10
                }
                current_x += 6
                if current_x >= 12:
                    current_x = 0
                    current_y += 10
                    
            pageDict["widgets"] = cards + otherWidgets
            dashboardConfig[pageId] = pageDict
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(dashboardConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = self.client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/dashboardConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"}) 
            
            # Updating insights.json
            for insight in insights.get("insights"):
                if insight.get("query") in uniqueQueries:
                    insight["isCharted"] = True
                else:
                    continue
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(insights, indent=4).encode("utf-8"))
                buffer.seek(0)
                self.client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/insights.json", file = buffer.getvalue(), file_options = {"upsert": "true"})  
            updateProjectModifiedAt(details.projectId)
            return dashboardConfig.get(pageId) 
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
                    selectedColumns=panelChartDetails.selectedColumns,
                    mapType=panelChartDetails.mapType,
                    isFilterApplied=panelChartDetails.isFilterApplied,
                    filters=panelChartDetails.filters,
                    geoCodeColumn=panelChartDetails.zipCodeColumn
                )
                generatedCodeTemplate = string.Template(self.codeTemplates.get("panelChartWithoutBlend"))
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
                    selectedColumns=panelChartDetails.selectedColumns,
                    mapType=panelChartDetails.mapType,
                    isFilterApplied=panelChartDetails.isFilterApplied,
                    filters=panelChartDetails.filters,
                    geoCodeColumn='"{}"'.format(panelChartDetails.zipCodeColumn) if panelChartDetails.zipCodeColumn else "None"
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
                    selectedColumns=panelChartDetails.selectedColumns,
                    mapType=panelChartDetails.mapType,
                    isFilterApplied=panelChartDetails.isFilterApplied,
                    filters=panelChartDetails.filters,
                    geoCodeColumn=panelChartDetails.zipCodeColumn
                )
                generatedCodeTemplate = string.Template(self.codeTemplates.get("panelChartWithBlend"))
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
                    selectedColumns=panelChartDetails.selectedColumns,
                    mapType=panelChartDetails.mapType,
                    isFilterApplied=panelChartDetails.isFilterApplied,
                    filters=panelChartDetails.filters,
                    geoCodeColumn='"{}"'.format(panelChartDetails.zipCodeColumn) if panelChartDetails.zipCodeColumn else "None"
                )
            else:
                pass
            response.update({"generatedCode": generatedCode})
            updateProjectModifiedAt(panelChartDetails.projectId)
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def saveQuery(self, details: SaveQuery) -> str:
        """
        Save a user-marked favourite query to a queryConfig.json file in the project's Supabase storage folder.

        Generates a unique ID for the query. If a queryConfig.json file already exists for the project, the new query is appended to it. Otherwise, a new queryConfig.json file is created.

        Args:
            details (SaveQuery): The details containing the project ID and the favourite query string.

        Returns:
            str: The unique query ID assigned to the saved query.

        Raises:
            CustomException: If saving the query configuration fails for any reason.
        """
        try:
            queryId = str(uuid.uuid4())
            allFiles = [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = details.projectId)]
            if "queryConfig.json" in allFiles:
                fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "queryConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                queryConfig = json.loads(urlopen(fileUrl).read())
                queryConfig[queryId] = details.query
            else:
                queryConfig = {queryId: details.query}
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(queryConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = self.client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/queryConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            updateProjectModifiedAt(details.projectId)
            return queryId
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def getQueries(self, projectId: str) -> dict:
        """
        Retrieve all saved favourite queries for a project from the queryConfig.json file.

        Args:
            projectId (str): The project identifier.

        Returns:
            dict: A dictionary of saved queries (query IDs as keys, query strings as values). Returns an empty dict if no queries have been saved.

        Raises:
            CustomException: If retrieval fails for any reason.
        """
        try:
            if "queryConfig.json" in [x.get("name") for x in self.client.storage.from_("AnalyticsHub").list(path = projectId)]:
                fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "queryConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                queryConfig = json.loads(urlopen(fileUrl).read())
            else:
                queryConfig = dict()
            return queryConfig
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def deleteQuery(self, details: DeleteQuery) -> None:
        """
        Delete a saved favourite query from the queryConfig.json file by its query ID.

        Args:
            details (DeleteQuery): The details containing the project ID and the query ID to delete.

        Raises:
            CustomException: If deletion fails for any reason.
        """
        try:
            fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "queryConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            queryConfig = json.loads(urlopen(fileUrl).read())
            queryConfig.pop(details.queryId)
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(queryConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = self.client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/queryConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            updateProjectModifiedAt(details.projectId)
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
    
reportingService = ReportingService()
