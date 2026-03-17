"""
metadataGenerator.py

This module provides components for generating metadata using a large language model.

It includes the MetadataGenerator class, which encapsulates the logic for creating
a LangChain runnable for metadata generation. The generator is configured via
external YAML and INI files for prompts and model parameters, respectively.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["MetadataGenerator"]        


from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from utils.exceptionHandler import CustomException
from analyticsHub.utils import readYaml, getConfig
from langchain_core.messages import AIMessage
# from langchain_openai import ChatOpenAI
from langchain_cerebras import ChatCerebras
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

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        """Removes <think> and </think> tokens from the AI message content.

        Args:
            inputStr (AIMessage): The input AI message.

        Returns:
            AIMessage: An AIMessage with the thinking tokens removed from its content.
        """
        inputStr = inputStr.content.replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getMetadataChain(self):
        """Constructs and returns a LangChain runnable for metadata generation.

        This method reads the configuration and prompt template, then builds a chain
        consisting of a prompt, a ChatCerebras model, and an output parser.
        The chain is designed to take a metadata dictionary as input.

        Returns:
            Runnable: A LangChain runnable sequence for generating metadata.

        Raises:
            CustomException: If any error occurs during chain construction.
        """
        try:
            logger.info("Constructing metadata generation chain.")
            self.config = getConfig(self.metadataGeneratorConfig.configPath)
            promptTemplate = readYaml(self.metadataGeneratorConfig.yamlPath).get("metadataGeneratorPrompt")
            prompt = ChatPromptTemplate.from_template(promptTemplate)
            llm = ChatCerebras(
                model = self.config.get("METADATAGENERATOR", "model"),
                temperature = self.config.getfloat("METADATAGENERATOR", "temperature")
            )
            outputParser = StrOutputParser()
            chain = {
                "metadata": RunnableLambda(lambda x: x.get("metadata"))
            } | prompt | llm | RunnableLambda(self._removeThinkTokens) | outputParser
            logger.info("Metadata generation chain constructed successfully.")
            return chain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception