"""
codeDebugger.py

This module provides the CodeDebugger class, which constructs a LangChain pipeline for debugging code using a Cerebras LLM.
It handles prompt construction, LLM invocation, and output post-processing for code debugging tasks.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["CodeDebugger"]


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
class CodeDebuggerConfig:
    """Configuration dataclass for CodeDebugger."""
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class CodeDebugger:
    """CodeDebugger constructs and manages a LangChain pipeline for code debugging using a Cerebras LLM."""
    def __init__(self):
        """Initializes the CodeDebugger instance and its configuration."""
        logger.info("Initializing CodeDebugger.")
        self.codeDebuggerConfig = CodeDebuggerConfig()

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        """
        Removes <think> and </think> tokens from the AIMessage content.

        Args:
            inputStr (AIMessage): The AIMessage object containing the LLM output.

        Returns:
            AIMessage: The AIMessage with <think> tokens removed from its content.
        """
        from utils.llm import cleanThinkTokens
        return cleanThinkTokens(inputStr)

    def getCodeDebuggerChain(self):
        """
        Constructs the LangChain pipeline for code debugging.

        Reads configuration and prompt template, initializes the Cerebras LLM, and composes the chain
        with prompt formatting, LLM invocation, output post-processing, and parsing.

        Returns:
            Runnable: The composed LangChain pipeline for code debugging.

        Raises:
            CustomException: If any error occurs during chain construction.
        """
        try:
            logger.info("Constructing code debugger chain.")
            self.config = getConfig(self.codeDebuggerConfig.configPath)
            promptTemplate = readYaml(self.codeDebuggerConfig.yamlPath).get("codeDebuggerAgentPrompt")
            codeGeneratorPrompt = PromptTemplate.from_template(promptTemplate)
            llm = getGenaiLlm(
                model=self.config.get("CODEDEBUGGER", "model"),
                temperature=self.config.getfloat("CODEDEBUGGER", "temperature"),
                max_tokens=self.config.getint("CODEDEBUGGER", "maxTokens", fallback=8192)
            )
            codeGeneratorParser = StrOutputParser()
            codeDebuggerChain = RunnablePassthrough() | codeGeneratorPrompt | llm | RunnableLambda(self._removeThinkTokens) | codeGeneratorParser
            logger.info("code debugger chain constructed successfully.")
            return codeDebuggerChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   