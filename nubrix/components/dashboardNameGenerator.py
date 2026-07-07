"""
dashboardNameGenerator.py

This module defines the DashboardNameGenerator class, which constructs a dashboard name generation
chain using LangChain and OpenRouter models. It takes KPI queries, chart types, and metadata context
to produce a concise, descriptive name for an automatically generated dashboard page.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["DashboardNameGenerator"]


from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from utils.llm import getGenaiLlm
from nubrix.utils import readYaml, getConfig
from utils.exceptionHandler import CustomException
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import AIMessage
from dataclasses import dataclass
from utils.logger import logger
import os

@dataclass
class DashboardNameGeneratorConfig:
    """
    Configuration dataclass for DashboardNameGenerator.

    Attributes:
        yamlPath (str): Path to the YAML file containing prompt templates.
        configPath (str): Path to the configuration file for model parameters.
    """
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class DashboardNameGenerator:
    """
    Generates a concise, descriptive dashboard name using an LLM based on the KPI queries and metadata context.
    """
    def __init__(self):
        """Initializes the DashboardNameGenerator instance and loads configuration paths."""
        logger.info("Initializing DashboardNameGenerator.")
        self.dashboardNameGeneratorConfig = DashboardNameGeneratorConfig()

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

    def getDashboardNameGeneratorChain(self):
        """
        Constructs the dashboard name generation chain using configuration and prompt templates.

        Returns:
            Runnable: The composed dashboard name generation chain for LangChain.

        Raises:
            CustomException: If any error occurs during chain construction.
        """
        try:
            logger.info("Constructing dashboard name generation chain.")
            self.config = getConfig(self.dashboardNameGeneratorConfig.configPath)
            promptTemplate = readYaml(self.dashboardNameGeneratorConfig.yamlPath).get("dashboardNameGeneratorPrompt")
            dashboardNamePrompt = PromptTemplate.from_template(promptTemplate)
            llm = getGenaiLlm(
                model=self.config.get("DASHBOARDNAMEGENERATOR", "model"),
                temperature=self.config.getfloat("DASHBOARDNAMEGENERATOR", "temperature"),
                max_tokens=self.config.getint("DASHBOARDNAMEGENERATOR", "maxTokens", fallback=8192)
            )
            dashboardNameParser = StrOutputParser()
            dashboardNameChain = RunnablePassthrough() | dashboardNamePrompt | llm | RunnableLambda(self._removeThinkTokens) | dashboardNameParser
            logger.info("Dashboard name generation chain constructed successfully.")
            return dashboardNameChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
