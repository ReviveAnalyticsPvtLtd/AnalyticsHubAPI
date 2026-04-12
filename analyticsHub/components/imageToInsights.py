"""
imageToInsights.py

This module provides the ImageToInsights class for analyzing base64-encoded dashboard images
and extracting meaningful, structured insights using a vision-capable model.
Supports a hybrid mode where chart data, statistical signals, and domain context are injected
alongside the image to produce evidence-backed, structured JSON insights.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["ImageToInsights"]

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from utils.llmOutputParser import parseModelJsonOutput
from utils.exceptionHandler import CustomException
from analyticsHub.utils import readYaml, getConfig
from dataclasses import dataclass
from utils.logger import logger
import json
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
    ImageToInsights provides functionality to analyze base64-encoded dashboard images and extract
    crisp, business-relevant insights using a language model.

    The analysis is guided by a custom system prompt defined in a YAML configuration file.
    When context is provided, the component operates in hybrid mode producing structured JSON output.
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
        
        self.llm = ChatGoogleGenerativeAI(
            model=self.config.get("IMAGETOINSIGHTS", "model"),
            temperature=self.config.getfloat("IMAGETOINSIGHTS", "temperature"),
            max_tokens=self.config.getint("IMAGETOINSIGHTS", "maxTokens", fallback=4096)
        )

    def _buildContextMessage(self, context: dict) -> str:
        """
        Serializes the context payload into a compact text block for the user message.

        Args:
            context (dict): Context payload from InsightContextBuilder + SignalEngine.

        Returns:
            str: Formatted context string for LLM consumption.
        """
        sections = []

        chartData = context.get("chartData", [])
        if chartData:
            sections.append(f"## chart_json_data\n{json.dumps(chartData, default=str, indent=2)}")

        stats = context.get("statisticalSummary", {})
        if stats:
            sections.append(f"## statistical_summary\n{json.dumps(stats, default=str, indent=2)}")

        domain = context.get("domainContext", {})
        if domain:
            sections.append(f"## domain_context\n{json.dumps(domain, default=str, indent=2)}")

        metadata = context.get("metadata", {})
        if metadata:
            sections.append(f"## database_metadata\n{json.dumps(metadata, default=str, indent=2)}")

        return "\n\n".join(sections)

    def getInsights(self, b64String: str, context: dict | None = None) -> dict | str:
        """
        Processes a base64-encoded image string (e.g., a dashboard screenshot)
        and returns extracted insights based on the model's interpretation.

        When context is provided, operates in hybrid mode: injects chart data,
        statistical signals, and domain context into the prompt and returns
        structured JSON output parsed into a dict.

        Args:
            b64String (str): A base64-encoded PNG image string representing a dashboard.
            context (dict | None): Optional context payload containing chartData,
                statisticalSummary, domainContext, and objectiveContext.

        Returns:
            dict: Structured insights (when context is provided) with keys
                diagnostic_insights, prescriptive_actions, missing_data.
            str: Raw insight text (legacy mode, when context is None).

        Raises:
            CustomException: If insight generation fails or any error occurs during processing.
        """
        try:
            logger.info("Generating insights from image.")

            userContent = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + b64String
                    }
                }
            ]

            if context:
                contextText = self._buildContextMessage(context)
                userContent.append({
                    "type": "text",
                    "text": contextText + "\n\nCRITICAL: You MUST output ONLY raw, valid JSON. Your response must be immediately parseable by `json.loads`. DO NOT wrap the output in markdown code blocks like ```json."
                })

            messages = [
                SystemMessage(content=self.prompt),
                HumanMessage(content=userContent)
            ]

            stopSequence = self.config.get("IMAGETOINSIGHTS", "stop", fallback="").strip()
            
            if stopSequence:
                response = self.llm.bind(stop=[stopSequence]).invoke(messages)
            else:
                response = self.llm.invoke(messages)

            rawOutput = response.content

            if context:
                return parseModelJsonOutput(rawOutput, "Image-to-insights")

            return rawOutput
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
