from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from ..utils.functions import readYaml, getConfig
from ..utils.exceptions import CustomException
from langchain_core.messages import AIMessage
from pydantic import Field, BaseModel
from langchain_cerebras import ChatCerebras
# from langchain_openai import ChatOpenAI
from ..utils.logger import logger
import os

class QueryRephraseOutput(BaseModel):
    rephrasedOutput: str | None = Field(
        description="A clear and concise rephrased version of the user's query. If the query is unclear, invalid, or requires clarification, this will be `None`."
    )
    doubt: str | None = Field(
        description="A message indicating any doubt, required clarification, or reason why the input query is invalid. If the query is successfully rephrased, this will be `None`."
    )

class QueryRephaser:
    def __init__(self):
        logger.info("Initializing QueryRephaser.")
        self.yamlPath = os.path.join(os.getcwd(), "params.yaml")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        inputStr = inputStr.text().replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getQueryRephraserChain(self):
        try:
            logger.info("Constructing query rephraser chain.")
            queryRephraseParser = JsonOutputParser(pydantic_object = QueryRephraseOutput)
            queryRephrasePrompt = PromptTemplate(
                template = readYaml(self.yamlPath)["queryRephraserAgentPrompt"],
                input_variables = ["metadata", "query"],
                partial_variables = {"format_instructions": queryRephraseParser.get_format_instructions()}
            )
            llm = ChatCerebras(
                model=self.config.get("QUERYREPHRASER", "model"),
                temperature=self.config.getfloat("QUERYREPHRASER", "temperature"),
                max_tokens=self.config.getint("QUERYREPHRASER", "maxTokens")
            )
            # llm = ChatOpenAI(
            #     openai_api_key = os.environ["OPENAI_API_KEY"],
            #     openai_api_base = os.environ["OPENAI_API_BASE"],
            #     model_name = self.config.get("QUERYREPHRASER", "model"),
            #     temperature = self.config.getfloat("QUERYREPHRASER", "temperature"),
            #     max_tokens = self.config.getint("QUERYREPHRASER", "maxTokens")
            # )
            queryRephraseChain = queryRephrasePrompt | llm | self._removeThinkTokens | queryRephraseParser
            logger.info("Query rephraser chain constructed successfully.")
            return queryRephraseChain
        except Exception as e:
            logger.error(f"Error constructing query rephraser chain: {e}")
            raise CustomException(e)