"""
webhooks.py

This module defines the FastAPI route for receiving Razorpay webhook
events. The endpoint verifies the webhook signature and delegates
event processing to the WebhookService.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["router"]


from utils.exceptionHandler import CustomException, raiseHttpException
from utils.webhookExceptions import RetryableWebhookError
from api.services.webhookService import webhookService
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, HTTPException, Request
import json

router = APIRouter()


@router.post("/razorpay")
async def razorpayWebhook(request: Request):
    """
    Receive and process Razorpay webhook events.

    Verifies the X-Razorpay-Signature header against the request body
    using HMAC SHA-256, then dispatches the event for processing.

    Args:
        request (Request): The incoming HTTP request from Razorpay.

    Returns:
        ORJSONResponse: Acknowledgement response.
    """
    try:
        body = await request.body()
        signature = request.headers.get("X-Razorpay-Signature", "")
        if not webhookService.verifyWebhookSignature(body, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
        try:
            event = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="Malformed webhook JSON payload")
        eventId = request.headers.get("X-Razorpay-Event-Id", "")
        webhookService.processEvent(event, eventId=eventId)
        return ORJSONResponse(
            status_code=200,
            content={"status": "ok"}
        )
    except HTTPException:
        raise
    except RetryableWebhookError as e:
        raiseHttpException(
            CustomException(
                exception=e,
                statusCode=503,
                uiMessage="Webhook processing dependency unresolved. Retrying later."
            )
        )
    except CustomException as e:
        raiseHttpException(e)
