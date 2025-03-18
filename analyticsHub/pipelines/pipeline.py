from ..components.metadataGenerator import MetadataGenerator
from ..workflows.reportingWorkflow import workflow
from ..utils.exceptions import CustomException
from ..utils.functions import readYaml
from supabase import create_client
from ..utils.logger import logger
import json
import os

class CompletePipeline:
    def __init__(self):
        logger.info("Initializing CompletePipeline components.")
        self.replManager = dict()
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
                results += self.replManager[projectId].run(codeString)
            metadataChain = self.metadataGenerator.getMetadataChain()
            metadata = metadataChain.invoke({"metadata": results})
            metadataParts = metadata.split("```")
            metadata = metadataParts[-2]
            metadata = json.loads("\n".join(metadata.split("\n")[1:]))
            return metadata
        except Exception as e:
            logger.error(f"Error during loadData: {e}")
            raise CustomException(e)

    def generateChart(self, inputQuery: str, metadata: dict, projectId: str) -> dict:
        try:
            response = workflow.invoke({
                "inputQuery": inputQuery,
                "metadata": metadata,
                "projectId": projectId
            })
            return response
        except Exception as e:
            logger.error(f"Error during loadData: {e}")
            raise CustomException(e)