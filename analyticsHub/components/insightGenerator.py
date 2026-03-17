"""
insightGenerator.py

This module defines the InsightGenerator class, which constructs an insight generation chain using LangChain and Cerebras models. It handles prompt loading, configuration, and output post-processing for insight generation tasks.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["InsightGenerator"]


from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from analyticsHub.utils import readYaml, getConfig
from utils.exceptionHandler import CustomException
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import AIMessage
# from langchain_openai import ChatOpenAI
from langchain_cerebras import ChatCerebras
from dataclasses import dataclass
from utils.logger import logger
import os

@dataclass
class InsightGeneratorConfig:
    """
    Configuration dataclass for InsightGenerator.

    Attributes:
        yamlPath (str): Path to the YAML file containing prompt templates.
        configPath (str): Path to the configuration file for model parameters.
    """
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class InsightGenerator:
    """
    InsightGenerator constructs and manages a code generation chain using LangChain and Cerebras LLMs.
    """
    def __init__(self):
        """Initializes the InsightGenerator instance and loads configuration paths."""
        logger.info("Initializing InsightGenerator.")
        self.insightGeneratorConfig = InsightGeneratorConfig()

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        """
        Removes <think> and </think> tokens from the AIMessage text.

        Args:
            inputStr (AIMessage): The AIMessage object containing the text to clean.

        Returns:
            AIMessage: A new AIMessage with <think> tokens removed.
        """
        inputStr = inputStr.content.replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getInsightGeneratorChain(self):
        """
        Constructs the insight generation chain using configuration and prompt templates.

        Returns:
            Runnable: The composed insight generation chain for LangChain.

        Raises:
            CustomException: If any error occurs during chain construction.
        """
        try:
            logger.info("Constructing insight generation chain.")
            self.config = getConfig(self.insightGeneratorConfig.configPath)
            promptTemplate = readYaml(self.insightGeneratorConfig.yamlPath).get("insightGeneratorAgentPrompt")
            insightGeneratorPrompt = PromptTemplate.from_template(promptTemplate)
            llm = ChatCerebras(
                model = self.config.get("INSIGHTGENERATOR", "model"),
                temperature = self.config.getfloat("INSIGHTGENERATOR", "temperature")
            )
            insightGeneratorParser = StrOutputParser()
            insightGeneratorChain = RunnablePassthrough() | insightGeneratorPrompt | llm | RunnableLambda(self._removeThinkTokens) | insightGeneratorParser
            logger.info("Insight generation chain constructed successfully.")
            return insightGeneratorChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception