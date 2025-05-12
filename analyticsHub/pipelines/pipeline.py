from ..components.metadataGenerator import MetadataGenerator
from ..workflows.reportingWorkflow import reportingToolWorkflow
from ..components.speechToText import SpeechToText
from ..utils.exceptions import CustomException
from ..utils.functions import readYaml
from ..components import replManager
from supabase import create_client
from urllib.request import urlopen
from ..utils.logger import logger
import json
import os

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
                codeString = readYaml(self.yamlPath)["attributeInfoCode"].format(dataframeName = dataframeName, projectId = projectId)
                results += replManager.manager[projectId].run(codeString)
            metadataChain = self.metadataGenerator.getMetadataChain()
            metadata = metadataChain.invoke({"metadata": results})
            metadataParts = metadata.split("```")
            metadata = metadataParts[-2]
            metadata = json.loads("\n".join(metadata.split("\n")[1:]))
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
            blendConfigUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "blendConfig.json").replace(".parquet", "")
            blendConfig = json.loads(urlopen(blendConfigUrl).read())
            blendedTables = list(blendConfig.keys())
            if dataSource in blendedTables:
                tablesUsed = blendConfig[dataSource].get("tables")
                joinTypes = blendConfig[dataSource].get("joinTypes")
                blendOn = blendConfig[dataSource].get("blendOn")
                response = replManager.manager[projectId].run(f"getDataForChart(projectId='{projectId}', chartType='{chartType}', xAxis='{xAxis}', yAxis='{yAxis}', aggregationMetric='{aggregationMetric}', tablesUsed={tablesUsed}, joinTypes={joinTypes}, blendOn={blendOn})")
            else:
                response = replManager.manager[projectId].run(f"getDataForChart(projectId='{projectId}', chartType='{chartType}', xAxis='{xAxis}', yAxis='{yAxis}', aggregationMetric='{aggregationMetric}', tablesUsed='{dataSource}')")    
            print("RESPONSE: ", response)
            logger.info(f"RESPONSE: {response}")
            response = json.loads(response)
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
        