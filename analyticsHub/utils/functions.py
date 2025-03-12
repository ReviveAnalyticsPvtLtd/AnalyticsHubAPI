from supabase import create_client
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