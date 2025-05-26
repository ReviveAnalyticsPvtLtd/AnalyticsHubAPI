from ..utils.functions import getConfig
from ..utils.exceptions import CustomException
from ..utils.logger import logger
from groq import Groq
import os

class SpeechToText:
    def __init__(self):
        logger.info("Initializing speech-to-text Model.")
        self.config = getConfig(os.path.join(os.getcwd(), "config.ini"))
        self.client = Groq()

    def getTranscript(self, b64String = str) -> str:
        try:
            logger.info("generating transcript.")
            transcription = self.client.audio.transcriptions.create(
                url = f'data:audio/webm;base64,{b64String}',
                model = self.config.get("SPEECHTOTEXT", "model")
            )
            return transcription.text.strip()
        except Exception as e:
            logger.error(f"Error constructing metadata generation chain: {e}")
            raise CustomException(e)