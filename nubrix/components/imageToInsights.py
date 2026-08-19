"""
imageToInsights.py

This module provides the ImageToInsights class for extracting structured, evidence-backed
insights from a dashboard page. The serialized dashboard data payload built by
DashboardPayloadBuilder is the sole input — the LLM reasons over exact numbers
and statistical signals, not screenshots.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["ImageToInsights"]

from utils.llm import getGenaiLlm
from langchain_core.messages import HumanMessage, SystemMessage
from utils.llmOutputParser import parseModelJsonOutput
from utils.exceptionHandler import CustomException
from nubrix.utils import readYaml, getConfig
from dataclasses import dataclass
from utils.logger import logger
import os

@dataclass
class ImageToInsightsConfig:
    """
    Configuration dataclass for ImageToInsights.

    Attributes:
        yamlPath (str): Path to the YAML file containing system prompts.
        configPath (str): Path to the configuration file containing model settings.
    """
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")

class ImageToInsights:
    """
    ImageToInsights analyses a serialized dashboard data payload and extracts crisp,
    business-relevant insights using a language model.

    The analysis is guided by a custom system prompt defined in a YAML configuration file.
    Output is always structured JSON. Row and token limits for the payload are read from
    config and exposed for the payload builder to consume.
    """
    def __init__(self):
        """
        Initializes the ImageToInsights instance:
            - Loads system prompt from YAML
            - Loads model configuration from config file
            - Sets up the ChatGoogleGenerativeAI client
        """
        logger.info("Initializing image-to-insight model.")
        self.imageToInsightsConfig = ImageToInsightsConfig()
        self.config = getConfig(self.imageToInsightsConfig.configPath)
        prompt = readYaml(filePath=self.imageToInsightsConfig.yamlPath).get("imageToInsightGeneratorPrompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Missing or invalid 'imageToInsightGeneratorPrompt' in prompts.yaml.")
        self.prompt = prompt
        
        self.llm = getGenaiLlm(
            model=self.config.get("IMAGETOINSIGHTS", "model"),
            max_tokens=self.config.getint("IMAGETOINSIGHTS", "maxTokens", fallback=4096)
        )

        self.payloadTokenBudget = self.config.getint("IMAGETOINSIGHTS", "payloadTokenBudget", fallback=12000)
        self.fullRowLimit = self.config.getint("IMAGETOINSIGHTS", "fullRowLimit", fallback=40)
        self.compactRowLimit = self.config.getint("IMAGETOINSIGHTS", "compactRowLimit", fallback=10)

    def getInsights(
        self,
        payloadText: str,
        context: dict | None = None,
        callbacks: list | None = None,
    ) -> dict:
        """
        Generates structured insights from a serialized dashboard data payload.

        Args:
            payloadText (str): Serialized dashboard payload from DashboardPayloadBuilder.
            context (dict | None): Context dict, read only for tracing identifiers.
            callbacks (list | None): LangChain callbacks, e.g. credit tracking.

        Returns:
            dict: Structured insights with keys diagnostic_insights,
                prescriptive_actions, missing_data.

        Raises:
            CustomException: If insight generation fails.
        """
        try:
            logger.info("Generating insights from dashboard data payload.")

            userContent = payloadText + (
                "\n\nCRITICAL: You MUST output ONLY raw, valid JSON. Your response must be "
                "immediately parseable by `json.loads`. DO NOT wrap the output in markdown "
                "code blocks like ```json."
            )

            messages = [
                SystemMessage(content=self.prompt),
                HumanMessage(content=userContent),
            ]

            stopSequence = self.config.get("IMAGETOINSIGHTS", "stop", fallback="").strip()

            from utils.llm import getLangfuseConfig
            projectId = context.get("projectId") if isinstance(context, dict) else None
            userId = context.get("userId") if isinstance(context, dict) else None
            config = getLangfuseConfig(trace_name="ImageToInsights", projectId=projectId, userId=userId)
            if callbacks:
                config.setdefault("callbacks", []).extend(callbacks)
            invokeConfig = config or None

            if stopSequence:
                response = self.llm.bind(stop=[stopSequence]).invoke(messages, config=invokeConfig)
            else:
                response = self.llm.invoke(messages, config=invokeConfig)

            return parseModelJsonOutput(response.content, "Image-to-insights")
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
