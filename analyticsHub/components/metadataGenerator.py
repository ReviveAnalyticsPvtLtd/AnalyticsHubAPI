from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from ..utils.functions import readYaml, getConfig
from ..utils.exceptions import CustomException
from langchain_groq import ChatGroq
from ..utils.logger import logger
import os

class MetadataGenerator:
    def __init__(self):
        logger.info("Initializing MetadataGenerator.")
        self.yamlPath = os.path.join(os.getcwd(), "params.yaml")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))

    def getMetadataChain(self):
        try:
            logger.info("Constructing metadata generation chain.")
            promptTemplate = readYaml(self.yamlPath)["metadataGeneratorPrompt"]
            prompt = ChatPromptTemplate.from_template(promptTemplate)
            llm = ChatGroq(
                model=self.config.get("METADATAGENERATOR", "model"),
                temperature=self.config.getfloat("METADATAGENERATOR", "temperature")
            )
            outputParser = StrOutputParser()
            chain = {
                "metadata": RunnableLambda(lambda x: x["metadata"])
            } | prompt | llm | outputParser
            logger.info("Metadata generation chain constructed successfully.")
            return chain
        except Exception as e:
            logger.error(f"Error constructing metadata generation chain: {e}")
            raise CustomException(e)