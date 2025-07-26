__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["celeryApp"]


from .tasks.generateForecasts import GenerateForecasts
from celery import Celery
import os

class CeleryWrapper:
    def __init__(self, name: str):
        self.name = name
        self._app = Celery(
            self.name,
            broker = self.redisUrl,
            backend = self.redisUrl
        )
        self._registerTasks()

    @property
    def redisUrl(self) -> str:
        return f'redis://default:{os.environ.get("REDIS_PASSWORD")}@{os.environ.get("REDIS_HOST")}:{os.environ.get("REDIS_PORT")}'

    @property
    def app(self):
        return self._app

    def _registerTasks(self):
        @self._app.task(name = f"{self.name}.generateForecasts")
        def sendForecasts():
            return GenerateForecasts().generateAndSendForecasts()

celeryApp = CeleryWrapper("AnalyticsHub").app