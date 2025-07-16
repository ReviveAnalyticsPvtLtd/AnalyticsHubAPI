from ..workflows.reportingWorkflow import reportingToolWorkflow
from ..components.metadataGenerator import MetadataGenerator
from ..utils.functions import readYaml, attributeInfoFunc
from ..components.reportGenerator import ReportGenerator
from concurrent.futures import ProcessPoolExecutor
from ..components.speechToText import SpeechToText
from ..utils.exceptions import CustomException
from ..components import replManager
from supabase import create_client
from urllib.request import urlopen
from ..utils.logger import logger
import orjson
import time
import os
import io

class CompletePipeline:
    def __init__(self):
        logger.info("Initializing CompletePipeline components.")
        self.speechToTextModule = SpeechToText()
        self.metadataGenerator = MetadataGenerator()
        self.yamlPath = os.path.join(os.getcwd(), "params.yaml")
        self.supabaseClient = create_client(
            supabase_url = os.environ["SUPABASE_URL"],
            supabase_key = os.environ["SUPABASE_KEY"]
        )

    def generateMetadata(self, projectId: str) -> dict:
        try:
            dataFiles = [x.get("name") for x in self.supabaseClient.storage.from_("AnalyticsHub").list(path = projectId) if x.get("name").endswith(".parquet")]
            results = ""
            for fileName in dataFiles:
                dataframeName = fileName.replace(".parquet", "")
                results += attributeInfoFunc(projectId = projectId, dataframeName = dataframeName)
                # codeString = readYaml(self.yamlPath)["attributeInfoCode"].format(dataframeName = dataframeName, projectId = projectId)
                # results += replManager.run(codeString)
            metadataChain = self.metadataGenerator.getMetadataChain()
            metadata = metadataChain.invoke({"metadata": results})
            metadataParts = metadata.split("```")
            metadata = metadataParts[-2]
            metadata = orjson.loads("\n".join(metadata.split("\n")[1:]).encode())
            return metadata
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)

    def generateChart(self, inputQuery: str, metadata: dict, projectId: str) -> dict:
        try:
            response = reportingToolWorkflow.invoke({
                "inputQuery": inputQuery,
                "metadata": metadata,
                "projectId": projectId
            })
            return response
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)
        
    def generateChartFromPanel(self, projectId: str, chartType: str, xAxis: str, yAxis: str, aggregationMetric: str, dataSource: str) -> dict:
        try:
            allFiles = [x.get("name") for x in self.supabaseClient.storage.from_("AnalyticsHub").list(path = projectId)]
            if "blendConfig.json" in allFiles:
                blendConfigUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "blendConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                blendConfig = orjson.loads(urlopen(blendConfigUrl).read())
                tablesUsed = blendConfig[dataSource].get("tables")
                joinTypes = blendConfig[dataSource].get("joinTypes")
                blendOn = blendConfig[dataSource].get("blendOn")
                response = replManager.run(f"getDataForChart(projectId='{projectId}', chartType='{chartType}', xAxis='{xAxis}', yAxis='{yAxis}', aggregationMetric='{aggregationMetric}', tablesUsed={tablesUsed}, joinTypes={joinTypes}, blendOn={blendOn})")
            else:
                response = replManager.run(f"getDataForChart(projectId='{projectId}', chartType='{chartType}', xAxis='{xAxis}', yAxis='{yAxis}', aggregationMetric='{aggregationMetric}', tablesUsed='{dataSource}')")    
            response = orjson.loads(response.encode())
            return response
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)

    def speechToText(self, b64String: str) -> str:
        try:
            return self.speechToTextModule.getTranscript(b64String = b64String)
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)
        
    def generateReport(self, projectId: str) -> dict:
        try:
            reportGeneratorObj = ReportGenerator()
            allTables = reportGeneratorObj.getAllTables(projectId = projectId)
            with ProcessPoolExecutor(max_workers = 5) as executor:
                futures = [executor.submit(reportGeneratorObj.getProfilingReport, projectId, tableName) for tableName in allTables]
            results = [x.result() for x in futures]
            dct = dict(zip(allTables, results))
            with io.BytesIO() as buffer:
                output = orjson.dumps(dct)
                buffer.write(output)
                buffer.seek(0)
                _ = self.supabaseClient.storage.from_("AnalyticsHub").upload(path = f"{projectId}/generatedReport.json", file = buffer.getvalue(), file_options = {"upsert": "true"})  
            return output.decode("utf-8")
        except Exception as e:
            logger.error(CustomException(e))
            raise CustomException(e)