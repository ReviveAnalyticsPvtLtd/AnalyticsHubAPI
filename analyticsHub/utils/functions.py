from supabase import create_client
import datetime
import os

client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

def verifyToken(token: str):
    token = token.split(" ")[1]
    allTokens = [x["accessToken"] for x in client.table("Sessions").select("accessToken").execute().data]
    if token in allTokens: 
        response = client.table("Sessions").update({"lastActivity": str(datetime.datetime.utcnow())}).eq("accessToken", token).execute()
        return True
    else: return False