from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from ..utils.functions import readYaml, getConfig
from ..utils.exceptions import CustomException
from langchain_cerebras import ChatCerebras
from ..utils.logger import logger
import os

class CodeGenerator:
    def __init__(self):
        logger.info("Initializing CodeGenerator.")
        self.yamlPath = os.path.join(os.getcwd(), "params.yaml")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))

    def getCodeGeneratorChain(self):
        try:
            logger.info("Constructing code generation chain.")
            promptTemplate = readYaml(self.yamlPath)["codeGeneratorAgentPrompt"]
            codeGeneratorPrompt = PromptTemplate.from_template(promptTemplate)
            llm = ChatCerebras(
                model=self.config.get("CODEGENERATOR", "model"),
                temperature=self.config.getfloat("CODEGENERATOR", "temperature")
            )
            codeGeneratorParser = StrOutputParser()
            codeGeneratorChain = RunnablePassthrough() | codeGeneratorPrompt | llm | codeGeneratorParser
            logger.info("code generation chain constructed successfully.")
            return codeGeneratorChain
        except Exception as e:
            logger.error(f"Error constructing code generation chain: {e}")
            raise CustomException(e)