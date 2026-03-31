"""
domainKpiMapper.py

This module defines the DomainKpiMapper class, which builds a domain KPI mapping
pipeline using LangChain and Cerebras models. It manages prompt retrieval,
configuration handling, and output cleaning for KPI insight generation.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["DomainKpiMapperConfig"]


from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI
from analyticsHub.utils import readYaml, getConfig
from utils.exceptionHandler import CustomException
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import AIMessage
from dataclasses import dataclass
from utils.logger import logger
import os

@dataclass
class DomainKpiMapperConfig:
    """
    Holds configuration paths for the DomainKpiMapper.

    Attributes:
        yamlPath (str): File path to the YAML file containing prompt templates.
        configPath (str): File path to the configuration file with model settings.
    """
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class DomainKpiMapper:
    """
    Builds and manages a domain-aware KPI mapping pipeline using LangChain and Cerebras LLMs.
    """
    def __init__(self):
        """Initializes the DomainKpiMapper instance and loads configuration file paths."""
        logger.info("Initializing DomainKpiMapper.")
        self.domaonKpiMapperConfig = DomainKpiMapperConfig()

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        """
        Cleans AIMessage text by removing <think> and </think> tags.

        Args:
            inputStr (AIMessage): The AIMessage object containing text to process.

        Returns:
            AIMessage: A new AIMessage object with tags removed.
        """
        inputStr = inputStr.content.replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getDomainKpiMapperChain(self):
        """
        Creates the domain KPI mapping chain using loaded prompts and configurations.

        Returns:
            Runnable: A runnable LangChain pipeline for domain KPI mapping.

        Raises:
            CustomException: If an error occurs during chain creation.
        """
        try:
            logger.info("Constructing domain KPI mapper chain.")
            self.config = getConfig(self.domaonKpiMapperConfig.configPath)
            promptTemplate = readYaml(self.domaonKpiMapperConfig.yamlPath).get("domainAwareKpiMappingAgentPrompt")
            domainAwareKpiMappingAgentPrompt = PromptTemplate.from_template(promptTemplate)
            llm = ChatGoogleGenerativeAI(
                model=self.config.get("DOMAINKPIMAPPER", "model"),
                temperature=self.config.getfloat("DOMAINKPIMAPPER", "temperature"),
                max_tokens=self.config.getint("DOMAINKPIMAPPER", "maxTokens", fallback=8192)
            )
            domainKpiMapperParser = StrOutputParser()
            domainKpiMapperChain = RunnablePassthrough() | domainAwareKpiMappingAgentPrompt | llm | RunnableLambda(self._removeThinkTokens) | domainKpiMapperParser
            logger.info("Domain KPI mapper chain created successfully.")
            return domainKpiMapperChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception