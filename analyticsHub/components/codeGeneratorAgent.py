from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.messages import AIMessage
from ..utils.functions import readYaml, getConfig
from ..utils.exceptions import CustomException
from langchain_cerebras import ChatCerebras
# from langchain_openai import ChatOpenAI
from ..utils.logger import logger
import os

class CodeGenerator:
    def __init__(self):
        logger.info("Initializing CodeGenerator.")
        self.yamlPath = os.path.join(os.getcwd(), "params.yaml")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))

    def _removeThinkTokens(self, inputStr: AIMessage) -> AIMessage:
        inputStr = inputStr.text().replace("<think>", "").replace("</think>", "")
        return AIMessage(inputStr)

    def getCodeGeneratorChain(self):
        try:
            logger.info("Constructing code generation chain.")
            promptTemplate = readYaml(self.yamlPath)["codeGeneratorAgentPrompt"]
            codeGeneratorPrompt = PromptTemplate.from_template(promptTemplate)
            llm = ChatCerebras(
                model=self.config.get("CODEGENERATOR", "model"),
                temperature=self.config.getfloat("CODEGENERATOR", "temperature")
            )
            # llm = ChatOpenAI(
            #     openai_api_key = os.environ["OPENAI_API_KEY"],
            #     openai_api_base = os.environ["OPENAI_API_BASE"],
            #     model_name = self.config.get("CODEGENERATOR", "model"),
            #     temperature = self.config.getfloat("CODEGENERATOR", "temperature"),
            #     max_tokens = self.config.getint("CODEGENERATOR", "maxTokens")
            # )
            codeGeneratorParser = StrOutputParser()
            codeGeneratorChain = RunnablePassthrough() | codeGeneratorPrompt | llm | self._removeThinkTokens | codeGeneratorParser
            logger.info("code generation chain constructed successfully.")
            return codeGeneratorChain
        except Exception as e:
            logger.error(f"Error constructing code generation chain: {e}")
            raise CustomException(e)