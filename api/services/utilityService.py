"""
UtilityService module provides utility functions such as speech-to-text transcription, sending forecasts, and sample data manipulations for AnalyticsHub.

Author: Rauhan Ahmed Siddiqui
Version: 1.0.0
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["utilityService"]


from analyticsHub.components.speechToText import SpeechToText
from utils.exceptionHandler import CustomException
from analyticsHub.triggers.celery import celeryApp
from api.models import SpeechToTextModel
from celery.result import AsyncResult
from utils.logger import logger
from api.commons import client
import seaborn as sns
import pandas as pd

class UtilityService:
    """
    Service class providing utility operations such as speech transcription, sending forecasts, and sample data manipulations.
    """
    def __init__(self) -> None:
        """
        Initializes the UtilityService, loads a sample dataset, and sets up required modules.
        """
        logger.info("Initializing Utility Service.")
        self.sampleDataset = sns.load_dataset("tips")
        self.speechToTextModule = SpeechToText()
        self.client = client
    
    def getSpeechTranscript(self, speechToText: SpeechToTextModel) -> str:
        """
        Converts base64-encoded audio to text using the SpeechToText module.

        Args:
            speechToText (SpeechToTextModel): Model containing the base64-encoded audio string.
        Returns:
            str: The transcribed text from the audio.
        Raises:
            CustomException: If transcription fails.
        """
        try:
            transcriptText = self.speechToTextModule.getTranscript(b64String = speechToText.b64String)
            return transcriptText
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
    def sendForecasts(self) -> AsyncResult:
        """
        Sends a task to generate forecasts asynchronously using Celery.

        Returns:
            AsyncResult: The result object for the Celery task.
        Raises:
            CustomException: If the task submission fails.
        """
        try:
            return celeryApp.send_task("AnalyticsHub.generateForecasts")
        except Exception as e:
            exception  = CustomException(e)
            logger.error(exception)
            raise exception     
        
    def tempFunc(self, num: int) -> str:
        """
        Returns different representations or aggregations of the sample dataset based on the input parameter.

        Args:
            num (int): Determines the type of data transformation to perform.
                1: Returns the dataset as a list of records.
                2: Returns a pivot table by day and sex.
                3: Returns a pivot table by day, sex, and time.
                Other: Returns a pivot table by day, sex, time, and includes tip.
        Returns:
            str: The resulting data as a JSON string or list of records.
        Raises:
            CustomException: If data processing fails.
        """
        try:
            if num == 1:
                result = self.sampleDataset.to_dict(orient = "records")
            elif num == 2:
                result = pd.pivot_table(self.sampleDataset, index="day", columns=["sex"], aggfunc="count", values=["total_bill"]).to_json()
            elif num == 3:
                result = pd.pivot_table(self.sampleDataset, index="day", columns=["sex", "time"], aggfunc="count", values=["total_bill"]).to_json()
            else:
                result = pd.pivot_table(self.sampleDataset, index="day", columns=["sex", "time"], aggfunc="count", values=["total_bill", "tip"]).to_json()
        except Exception as e:      
            exception  = CustomException(e)
            logger.error(exception)
            raise exception
        
utilityService = UtilityService()