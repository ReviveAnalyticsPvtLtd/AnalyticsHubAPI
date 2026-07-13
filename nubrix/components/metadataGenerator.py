"""
metadataGenerator.py

MetadataGenerator: builds a LangChain chain for project-metadata generation
from prompts.yaml + config.ini.
"""

from langchain_core.output_parsers import StrOutputParser
from utils.llm import getGenaiLlm, cleanThinkTokens
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from utils.exceptionHandler import CustomException
from nubrix.utils import readYaml, getConfig
from dataclasses import dataclass
from utils.logger import logger
import os

@dataclass
class MetadataGeneratorConfig:
    """Configuration for the MetadataGenerator."""
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class MetadataGenerator:
    """A class for generating metadata using a large language model chain."""
    def __init__(self):
        """Initializes the MetadataGenerator."""
        logger.info("Initializing MetadataGenerator.")
        self.metadataGeneratorConfig = MetadataGeneratorConfig()

    def getMetadataChain(self):
        """Constructs and returns a LangChain runnable for metadata generation."""
        try:
            logger.info("Constructing metadata generation chain.")
            self.config = getConfig(self.metadataGeneratorConfig.configPath)
            promptTemplate = readYaml(self.metadataGeneratorConfig.yamlPath).get("metadataGeneratorPrompt")
            prompt = ChatPromptTemplate.from_template(promptTemplate)
            llm = getGenaiLlm(
                model=self.config.get("METADATAGENERATOR", "model"),
                temperature=self.config.getfloat("METADATAGENERATOR", "temperature"),
                max_tokens=self.config.getint("METADATAGENERATOR", "maxTokens", fallback=8192)
            )
            outputParser = StrOutputParser()
            chain = {
                "metadata": RunnableLambda(lambda x: x.get("metadata"))
            } | prompt | llm | RunnableLambda(cleanThinkTokens) | outputParser
            logger.info("Metadata generation chain constructed successfully.")
            return chain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception