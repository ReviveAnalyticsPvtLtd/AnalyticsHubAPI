"""
Celery application setup for AnalyticsHub.

This module defines a Celery application wrapper for managing background tasks,
specifically for generating and sending forecasts. It configures the Celery app
with Redis as the broker and backend, and registers the available tasks.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["celeryApp"]

from tasks.generateForecasts import GenerateForecasts
from celery import Celery
import os

class CeleryWrapper:
    """
    Wrapper class for initializing and configuring a Celery application.

    Attributes:
        name (str): The name of the Celery application.
        _app (Celery): The Celery application instance.

    Methods:
        redisUrl: Returns the Redis URL for broker and backend.
        app: Returns the Celery application instance.
        _registerTasks: Registers Celery tasks with the application.
    """
    def __init__(self, name: str):
        """
        Initialize the CeleryWrapper with the given application name.

        Args:
            name (str): The name to assign to the Celery application.
        """
        self.name = name
        self._app = Celery(
            self.name,
            broker = self.redisUrl,
            backend = self.redisUrl
        )
        self._registerTasks()

    @property
    def redisUrl(self) -> str:
        """
        Construct the Redis URL using environment variables.

        Returns:
            str: The Redis connection URL for Celery broker and backend.
        """
        return f'redis://default:{os.environ.get("REDIS_PASSWORD")}@{os.environ.get("REDIS_HOST")}:{os.environ.get("REDIS_PORT")}'

    @property
    def app(self):
        """
        Get the Celery application instance.

        Returns:
            Celery: The configured Celery application.
        """
        return self._app

    def _registerTasks(self):
        """
        Register Celery tasks with the application.

        This method defines and registers the 'generateForecasts' task,
        which triggers the GenerateForecasts workflow.
        """
        @self._app.task(name = f"{self.name}.generateForecasts")
        def sendForecasts():
            return GenerateForecasts().generateAndSendForecasts()

celeryApp = CeleryWrapper("AnalyticsHub").app