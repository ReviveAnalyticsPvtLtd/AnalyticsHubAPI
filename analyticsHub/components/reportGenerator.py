from ydata_profiling import ProfileReport
from urllib.request import urlopen
from bs4 import BeautifulSoup
import pandas as pd
import json
import os

class ReportGenerator:
    def __init__(self):
        pass

    def getAllTables(self, projectId: str) -> list:
        metadataUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "")
        metadata = json.loads(urlopen(metadataUrl).read().decode("utf-8"))
        tables = list(metadata.keys())
        return tables

    def getProfilingReport(self, projectId: str, tableName: str) -> str:
        df = pd.read_parquet(os.environ["FILE_URL"].format(projectId = projectId, fileName = tableName))
        htmlText = ProfileReport(df, title = f"Report for: {tableName}").to_html()
        soup = BeautifulSoup(htmlText, 'html.parser')
        soup.find("button", id = "tab-overview-reproduction").decompose()
        soup.find("div", id = "tab-pane-overview-reproduction").decompose()
        soup.find("p", class_ = "text-body-secondary text-end").decompose()
        soup.find("footer").decompose()
        return str(soup)