from celery.app import Celery
from .generateForecasts import generateAndSendForecasts
import os

redisUrl = f"redis://default:{os.environ.get("REDIS_PASSWORD")}@{os.environ.get("REDIS_HOST")}:{os.environ.get("REDIS_PORT")}"

celeryApp = Celery(
    "AnalyticsHub",
    broker = redisUrl,
    backend = redisUrl
)

@celeryApp.task
def sendForecasts():
    taskResposeStatus = generateAndSendForecasts()
    return taskResposeStatus