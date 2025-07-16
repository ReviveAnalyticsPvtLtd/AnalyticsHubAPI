from supabase import create_client
import pandas as pd
import configparser
import datetime
import yaml
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