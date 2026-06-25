"""
queryRephraser.py

This module contains the QueryRephaser agent for rephrasing user queries.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["QueryRephaser", "ParallelQueryRephaser"]        


from langchain_core.output_parsers import JsonOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.runnables import RunnableLambda
from utils.exceptionHandler import CustomException
from nubrix.utils import readYaml, getConfig
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import AIMessage
from pydantic import Field, BaseModel
from dataclasses import dataclass
from utils.logger import logger
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
        Removes '<think>' and '</think>' tokens (and the text inside them) from the AIMessage content,
        and extracts the raw JSON string.
        
        Args:
            inputStr (AIMessage): The input AIMessage from the language model.
            
        Returns:
            AIMessage: A new AIMessage containing only the clean JSON string.
        """
        import re
        content = inputStr.content
        # Remove <think>...</think> completely (non-greedy)
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        
        # Extract JSON block
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1).strip()
        else:
            json_match_any = re.search(r'```\s*(.*?)\s*```', content, re.DOTALL)
            if json_match_any:
                content = json_match_any.group(1).strip()
            else:
                start = content.find('{')
                end = content.rfind('}')
                if start != -1 and end != -1 and end > start:
                    content = content[start:end+1].strip()
        
        return AIMessage(content.strip())

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
            llm = ChatGoogleGenerativeAI(
                model=self.config.get("QUERYREPHRASER", "model"),
                temperature=self.config.getfloat("QUERYREPHRASER", "temperature"),
                max_tokens=self.config.getint("QUERYREPHRASER", "maxTokens", fallback=8192)
            )
            queryRephraseChain = queryRephrasePrompt | llm | RunnableLambda(self._removeThinkTokens) | queryRephraseParser
            logger.info("Query rephraser chain constructed successfully.")
            return queryRephraseChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception


class ParallelQueryRephraseOutput(BaseModel):
    """Pydantic model for the structured output of the parallel query rephraser agent. No doubt field."""
    rephrasedOutput: str = Field(
        description="A clear and concise rephrased version of the user's query with step-by-step transformation instructions. This must ALWAYS be provided."
    )

class ParallelQueryRephaser(QueryRephaser):
    """An agent that rephrases queries for parallel generation without issuing doubts."""
    def getQueryRephraserChain(self):
        try:
            logger.info("Constructing parallel query rephraser chain.")
            self.config = getConfig(self.queryRephraserConfig.configPath)
            queryRephraseParser = JsonOutputParser(pydantic_object = ParallelQueryRephraseOutput)
            queryRephrasePrompt = PromptTemplate(
                template = readYaml(self.queryRephraserConfig.yamlPath).get("parallelQueryRephraserAgentPrompt"),
                input_variables = ["metadata", "query"],
                partial_variables = {"format_instructions": queryRephraseParser.get_format_instructions()}
            )
            llm = ChatGoogleGenerativeAI(
                model=self.config.get("QUERYREPHRASER", "model"),
                temperature=self.config.getfloat("QUERYREPHRASER", "temperature"),
                max_tokens=self.config.getint("QUERYREPHRASER", "maxTokens", fallback=8192)
            )
            queryRephraseChain = queryRephrasePrompt | llm | RunnableLambda(self._removeThinkTokens) | queryRephraseParser
            logger.info("Parallel query rephraser chain constructed successfully.")
            return queryRephraseChain
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception