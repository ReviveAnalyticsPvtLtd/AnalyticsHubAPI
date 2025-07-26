"""
codeGenerator.py

This module defines the CodeGenerator class, which constructs a code generation chain using LangChain and Cerebras models. It handles prompt loading, configuration, and output post-processing for code generation tasks.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["CodeGenerator"]        


from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from ...utils.exceptionHandler import CustomException
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import AIMessage
from langchain_cerebras import ChatCerebras
from ..utils import readYaml, getConfig
from ...utils.logger import logger
from dataclasses import dataclass
import os

@dataclass
class CodeGeneratorConfig:
    """
    Configuration dataclass for CodeGenerator.

    Attributes:
        yamlPath (str): Path to the YAML file containing prompt templates.
        configPath (str): Path to the configuration file for model parameters.
    """
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class CodeGenerator:
    """
    CodeGenerator constructs and manages a code generation chain using LangChain and Cerebras LLMs.
    """
    def __init__(self):
        """Initializes the CodeGenerator instance and loads configuration paths."""
        logger.info("Initializing CodeGenerator.")
        self.codeGeneratorConfig = CodeGeneratorConfig()

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        """
        Removes <think> and </think> tokens from the AIMessage text.

        Args:
            inputStr (AIMessage): The AIMessage object containing the text to clean.

        Returns:
            AIMessage: A new AIMessage with <think> tokens removed.
        """
        inputStr = inputStr.content().replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getCodeGeneratorChain(self):
        """
        Constructs the code generation chain using configuration and prompt templates.

        Returns:
            Runnable: The composed code generation chain for LangChain.

        Raises:
            CustomException: If any error occurs during chain construction.
        """
        try:
            logger.info("Constructing code generation chain.")
            self.config = getConfig(self.codeGeneratorConfig.configPath)
            promptTemplate = readYaml(self.codeGeneratorConfig.yamlPath).get("codeGeneratorAgentPrompt")
            codeGeneratorPrompt = PromptTemplate.from_template(promptTemplate)
            llm = ChatCerebras(
                model = self.config.get("CODEGENERATOR", "model"),
                temperature = self.config.getfloat("CODEGENERATOR", "temperature")
            )
            codeGeneratorParser = StrOutputParser()
            codeGeneratorChain = RunnablePassthrough() | codeGeneratorPrompt | llm | RunnableLambda(self._removeThinkTokens) | codeGeneratorParser
            logger.info("Code generation chain constructed successfully.")
            return codeGeneratorChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception