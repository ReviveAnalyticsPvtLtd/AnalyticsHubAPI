"""
speechToText.py

This module provides the SpeechToText class for converting base64-encoded audio data to text using a configurable speech-to-text model via the Groq API.
It handles model configuration, audio transcription, and error logging.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["SpeechToText"]        


from utils.exceptionHandler import CustomException
from analyticsHub.utils import getConfig
from utils.logger import logger
from groq import Groq
import os

class SpeechToText:
    """
    SpeechToText provides functionality to transcribe base64-encoded audio data to text using a speech-to-text model.
    """
    def __init__(self):
        """
        Initializes the SpeechToText instance, loads configuration, and sets up the Groq client.
        """
        logger.info("Initializing speech-to-text Model.")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))
        self.client = Groq()

    def getTranscript(self, b64String = str) -> str:
        """
        Converts a base64-encoded audio string to a text transcript using the configured speech-to-text model.

        Args:
            b64String (str): The base64-encoded audio string (default: str).

        Returns:
            str: The transcribed text from the audio input.

        Raises:
            CustomException: If transcription fails or an error occurs in the process.
        """
        try:
            logger.info("generating transcript.")
            transcription = self.client.audio.transcriptions.create(
                url = f'data:audio/webm;base64,{b64String}',
                model = self.config.get("SPEECHTOTEXT", "model")
            )
            return transcription.text.strip()
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception