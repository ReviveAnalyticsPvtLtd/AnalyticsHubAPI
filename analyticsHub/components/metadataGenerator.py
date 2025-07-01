from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from ..utils.functions import readYaml, getConfig
from ..utils.exceptions import CustomException
from langchain_core.messages import AIMessage
from langchain_cerebras import ChatCerebras
# from langchain_openai import ChatOpenAI
from ..utils.logger import logger
import os

class MetadataGenerator:
    def __init__(self):
        logger.info("Initializing MetadataGenerator.")
        self.yamlPath = os.path.join(os.getcwd(), "params.yaml")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        inputStr = inputStr.text().replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getMetadataChain(self):
        try:
            logger.info("Constructing metadata generation chain.")
            promptTemplate = readYaml(self.yamlPath)["metadataGeneratorPrompt"]
            prompt = ChatPromptTemplate.from_template(promptTemplate)
            llm = ChatCerebras(
                model=self.config.get("METADATAGENERATOR", "model"),
                temperature=self.config.getfloat("METADATAGENERATOR", "temperature")
            )
            # llm = ChatOpenAI(
            #     openai_api_key = os.environ["OPENAI_API_KEY"],
            #     openai_api_base = os.environ["OPENAI_API_BASE"],
            #     model_name = self.config.get("METADATAGENERATOR", "model"),
            #     temperature = self.config.getfloat("METADATAGENERATOR", "temperature"),
            #     max_tokens = self.config.getint("METADATAGENERATOR", "maxTokens")
            # )
            outputParser = StrOutputParser()
            chain = {
                "metadata": RunnableLambda(lambda x: x["metadata"])
            } | prompt | llm | self._removeThinkTokens | outputParser
            logger.info("Metadata generation chain constructed successfully.")
            return chain
        except Exception as e:
            logger.error(f"Error constructing metadata generation chain: {e}")
            raise CustomException(e)