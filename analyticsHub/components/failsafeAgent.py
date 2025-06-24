from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from ..utils.functions import readYaml, getConfig
from ..utils.exceptions import CustomException
# from langchain_cerebras import ChatCerebras
from langchain_openai import ChatOpenAI
from ..utils.logger import logger
import os

class FailsafeCodeGenerator:
    def __init__(self):
        logger.info("Initializing Failsafe.")
        self.yamlPath = os.path.join(os.getcwd(), "params.yaml")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))

    def getFailsafeCodeGeneratorChain(self):
        try:
            logger.info("Constructing failsafe code generation chain.")
            promptTemplate = readYaml(self.yamlPath)["codeDebuggerAgentPrompt"]
            codeGeneratorPrompt = PromptTemplate.from_template(promptTemplate)
            # llm = ChatCerebras(
            #     model=self.config.get("FAILSAFECODEGENERATOR", "model"),
            #     temperature=self.config.getfloat("FAILSAFECODEGENERATOR", "temperature")
            # )
            llm = ChatOpenAI(
                openai_api_key = os.environ["OPENAI_API_KEY"],
                openai_api_base = os.environ["OPENAI_API_BASE"],
                model_name = self.config.get("FAILSAFECODEGENERATOR", "model"),
                temperature = self.config.getfloat("FAILSAFECODEGENERATOR", "temperature"),
                max_tokens = self.config.getint("FAILSAFECODEGENERATOR", "maxTokens")
            )
            codeGeneratorParser = StrOutputParser()
            failsafeCodeGeneratorChain = RunnablePassthrough() | codeGeneratorPrompt | llm | codeGeneratorParser
            logger.info("failsafe code generation chain constructed successfully.")
            return failsafeCodeGeneratorChain
        except Exception as e:
            logger.error(f"Error constructing failsafe code generation chain: {e}")
            raise CustomException(e)