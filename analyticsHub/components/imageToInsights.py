"""
imageToInsights.py

This module provides the ImageToInsights class for analyzing base64-encoded dashboard images 
and extracting meaningful, structured insights using a language model via the Groq API.

It handles prompt loading, model configuration, image input formatting, and safe API interaction.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["ImageToInsights"]

from utils.exceptionHandler import CustomException
from analyticsHub.utils import readYaml, getConfig
from dataclasses import dataclass
from utils.logger import logger
from groq import Groq
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
    """
    def __init__(self):
        """
        Initializes the ImageToInsights instance:
            - Loads system prompt from YAML
            - Loads model configuration from config file
            - Sets up the Groq API client
        """
        logger.info("Initializing image-to-insight model.")
        self.imageToInsightsConfig = ImageToInsightsConfig()
        self.config = getConfig(self.imageToInsightsConfig.configPath)
        self.prompt = readYaml(filePath=self.imageToInsightsConfig.yamlPath).get("imageToInsightGeneratorPrompt")
        self.client = Groq()

    def getInsights(self, b64String: str) -> str:
        """
        Processes a base64-encoded image string (e.g., a dashboard screenshot)
        and returns extracted insights based on the model's interpretation.

        Args:
            b64String (str): A base64-encoded PNG image string representing a dashboard.

        Returns:
            str: Text containing structured insights extracted from the image.

        Raises:
            CustomException: If insight generation fails or any error occurs during processing.
        """
        try:
            logger.info("Generating insights from image.")
            completion = self.client.chat.completions.create(
                model=self.config.get("IMAGETOINSIGHTS", "model"),
                messages=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": self.prompt
                            }
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64," + b64String
                                }
                            }
                        ]
                    }
                ],
                temperature=1,
                max_completion_tokens=1024,
                top_p=1,
                stream=False,
                stop=None,
            )
            return completion.choices[0].message.content
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception