"""
queryRephraser.py

This module contains the QueryRephaser agent for rephrasing user queries.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["QueryRephaser"]        


from langchain_core.output_parsers import JsonOutputParser
from ...utils.exceptionHandler import CustomException
from langchain_core.runnables import RunnableLambda
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import AIMessage
from langchain_cerebras import ChatCerebras
from ..utils import readYaml, getConfig
from pydantic import Field, BaseModel
from ...utils.logger import logger
from dataclasses import dataclass
import os

@dataclass
class QueryRephraserConfig:
    """Configuration for the QueryRephaser."""
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class QueryRephraseOutput(BaseModel):
    """Pydantic model for the structured output of the query rephraser agent."""
    rephrasedOutput: str | None = Field(
        description="A clear and concise rephrased version of the user's query. If the query is unclear, invalid, or requires clarification, this will be `None`."
    )
    doubt: str | None = Field(
        description="A message indicating any doubt, required clarification, or reason why the input query is invalid. If the query is successfully rephrased, this will be `None`."
    )

class QueryRephaser:
    """An agent that rephrases a user's query to be clearer and more specific."""
    def __init__(self):
        """Initializes the QueryRephaser and its configuration."""
        logger.info("Initializing QueryRephaser.")
        self.queryRephraserConfig = QueryRephraserConfig()

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        """
        Removes '<think>' and '</think>' tokens from the AIMessage content.
        
        Args:
            inputStr (AIMessage): The input AIMessage from the language model.
            
        Returns:
            AIMessage: A new AIMessage with the think tokens removed.
        """
        inputStr = inputStr.content().replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getQueryRephraserChain(self):
        """
        Constructs and returns a LangChain chain for query rephrasing.

        The chain consists of a prompt, a Cerebras LLM, a token remover, 
        and a JSON output parser.

        Raises:
            CustomException: If there is an error during chain construction.

        Returns:
            A LangChain runnable chain.
        """
        try:
            logger.info("Constructing query rephraser chain.")
            self.config = getConfig(self.queryRephraserConfig.configPath)
            queryRephraseParser = JsonOutputParser(pydantic_object = QueryRephraseOutput)
            queryRephrasePrompt = PromptTemplate(
                template = readYaml(self.queryRephraserConfig.yamlPath).get("queryRephraserAgentPrompt"),
                input_variables = ["metadata", "query"],
                partial_variables = {"format_instructions": queryRephraseParser.get_format_instructions()}
            )
            llm = ChatCerebras(
                model=self.config.get("QUERYREPHRASER", "model"),
                temperature=self.config.getfloat("QUERYREPHRASER", "temperature"),
                max_tokens=self.config.getint("QUERYREPHRASER", "maxTokens")
            )
            queryRephraseChain = queryRephrasePrompt | llm | RunnableLambda(self._removeThinkTokens) | queryRephraseParser
            logger.info("Query rephraser chain constructed successfully.")
            return queryRephraseChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception