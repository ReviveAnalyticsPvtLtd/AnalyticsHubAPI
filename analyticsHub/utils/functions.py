from supabase import create_client
import pandas as pd
import configparser
import numpy as np
import datetime
import json
import yaml
import math
import os

client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

def verifyToken(token: str):
    allTokens = [x["accessToken"] for x in client.table("Sessions").select("accessToken").execute().data]
    if token in allTokens: 
        response = client.table("Sessions").update({"lastActivity": str(datetime.datetime.utcnow())}).eq("accessToken", token).execute()
        return True
    else: return False

def readYaml(filePath: str) -> dict:
    with open(filePath, "r") as f:
        content = yaml.safe_load(f)
    return content 

def getConfig(path: str) -> dict:
    config = configparser.ConfigParser()
    config.read(path)
    return config

def getDataTypes(projectId: str, tableName: str) -> list[dict]:   
    fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = tableName)
    df = pd.read_parquet(fileUrl)
    numericals = ["int64", "float64", "float32", "int32"]
    categoricals = ["bool", "category", "object", "string"]
    datetimeTypes = ["datetime64[ns]", "datetime64[ns, tz]"]
    allColumns = list()
    for column in df.columns:
        dtype = df[column].dtype
        if dtype in numericals:
            columnInfo = dict()
            columnInfo["columnName"] = column
            columnInfo["type"] = dtype.name
            columnInfo["min"] = df[column].min()
            columnInfo["max"] = df[column].max()
            allColumns.append(columnInfo)
        elif df[column].dtype in datetimeTypes:
            columnInfo = dict()
            columnInfo["columnName"] = column
            columnInfo["type"] = dtype.name
            columnInfo["min"] = df[column].min()
            columnInfo["max"] = df[column].max()
            allColumns.append(columnInfo)
        else:
            columnInfo = dict()
            columnInfo["columnName"] = column
            columnInfo["type"] = dtype.name
            columnInfo["uniqueValues"] = df[column].unique().tolist()
            allColumns.append(columnInfo)
    return allColumns

def attributeInfoFunc(projectId: str, dataframeName: str) -> str:
    df = pd.read_parquet(os.environ["FILE_URL"].format(projectId = projectId, fileName = dataframeName))
    attributeInfo = f'DATAFRAME NAME: {dataframeName}\n'
    for column in df.columns: attributeInfo += '- ' + str(column) + ' (' + df.get(column).dtype.name + ')\n'
    attributeInfo += 'SHAPE: ' + str(df.shape) + '\n'
    attributeInfo += 'SAMPLE ROW:\n' + str(df.loc[df.index[:1]].to_string()) + '\n'
    return attributeInfo

def serializer(obj):
    # Handle NumPy types
    if isinstance(obj, (np.integer)):
        return obj.item()  # Convert to native Python int
    elif isinstance(obj, (np.floating)):
        if np.isnan(obj) or np.isinf(obj):
            return None  # Replace NaN/Infinity with JSON-compliant null
        return obj.item()  # Convert to native Python float
    elif isinstance(obj, np.ndarray):
        return obj.tolist()  # Convert NumPy array to list
    elif isinstance(obj, np.datetime64):
        return str(obj)  # Convert to ISO 8601 string
    # Handle Pandas DataFrames and Series
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")  # Convert to list of dicts
    elif isinstance(obj, pd.Series):
        return obj.tolist()  # Convert Series to list
    # Handle datetime types
    elif isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()  # Convert to ISO 8601 string
    # Handle sets and tuples
    elif isinstance(obj, (set, tuple)):
        return list(obj)
    # Handle complex numbers
    elif isinstance(obj, complex):
        return {"real": obj.real, "imag": obj.imag}

def fetch_data(projectId: str, tableName: str, baseFilters: list = list()):
    import pandas as pd
    import redis
    import os
    import io
    r = redis.Redis(host=os.environ["REDIS_HOST"], port=int(os.environ["REDIS_PORT"]), password=os.environ["REDIS_PASSWORD"])
    key = f"{projectId}::{tableName}"
    df = r.get(key)
    if df is None:
        buffer = io.BytesIO()
        df = pd.read_parquet(os.environ["FILE_URL"].format(projectId = projectId, fileName = tableName))
        df.to_parquet(buffer, compression = "snappy")
        r.set(name = key, value = buffer.getvalue(), ex = 60)
    else:
        df = pd.read_parquet(io.BytesIO(df))

    if baseFilters:
        for filter in baseFilters:
            for column, condition in filter.items():
                columnTable, column = column.split(".")
                if columnTable == tableName: 
                    if column not in df.columns:
                        continue

                    if isinstance(condition, dict):
                        if df[column].dtype == "object":
                            if "contains" in condition:
                                df = df[df[column].str.contains(condition["contains"], case=False, na=False)]
                                continue
                            if "startswith" in condition:
                                df = df[df[column].str.startswith(condition["startswith"], na=False)]
                                continue
                            if "endswith" in condition:
                                df = df[df[column].str.endswith(condition["endswith"], na=False)]
                                continue
                        if "min" in condition:
                            df = df[df[column] >= condition["min"]]
                            continue
                        if "max" in condition:
                            df = df[df[column] <= condition["max"]]
                            continue

                    if isinstance(condition, (list, tuple, set)):
                        df = df[df[column].isin(condition)]
                        continue
                    else:
                        df = df[df[column] == condition]
                        continue
                else:
                    continue
    return df

def getDataForChart(projectId: str, chartType: str, xAxis: str, yAxis: str, aggregationMetric: str, tablesUsed: list[str] | str, joinTypes: list[str] | None = None, blendOn: list[str] | None = None):
    if isinstance(tablesUsed, list):
        allTables = [fetch_data(projectId, x) for x in tablesUsed]
        result = allTables[0]
        for i in range(len(joinTypes)):
            result = pd.merge(left = result, right = allTables[i+1], on = blendOn[i], how = joinTypes[i], suffixes = ['_left', '_right'])
    else:
        result = fetch_data(projectId, tablesUsed)
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
        # For card type, ensure we return a single value
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
    print(json.dumps(response, indent=4, default=serializer))