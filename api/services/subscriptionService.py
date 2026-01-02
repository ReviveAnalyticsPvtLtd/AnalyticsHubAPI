"""
subscriptionService.py

This module provides the SubscriptionService class, which encapsulates
all business logic related to user subscriptions, including free trials
and Razorpay subscription checkout verification (MVP).
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["subscriptionService"]


from utils.exceptionHandler import CustomException
from utils.logger import logger
from api.commons import client
import datetime
import os
import hmac
import hashlib
import razorpay


class SubscriptionService:
    """
    Service class for user subscription management.

    Handles free trials, Razorpay subscription creation,
    and checkout signature verification.
    """

    def __init__(self) -> None:
        """
        Initialize the SubscriptionService.
        """
        logger.info("Initializing Subscription Service.")
        self.client = client
        self.razorpayClient = razorpay.Client(
            auth=(
                os.environ["RAZORPAY_KEY_ID"],
                os.environ["RAZORPAY_KEY_SECRET"]
            )
        )

    def activateFreeTrial(self, userId: str) -> None:
        """
        Activate a free trial for a user.

        Args:
            userId (str): The ID of the user to activate the free trial for.
        """
        try:
            currentTime = datetime.datetime.utcnow()
            trialDurationDays = 12

            updateData = {
                "subscriptionPlan": "free",
                "subscriptionStart": currentTime.isoformat(),
                "subscriptionExpiry": (
                    currentTime + datetime.timedelta(days=trialDurationDays)
                ).isoformat()
            }

            self.client.table("Users").update(
                updateData
            ).eq("userId", userId).execute()

            return

        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def createSubscription(self, planId: str) -> dict:
        """
        Create a Razorpay subscription.

        Args:
            planId (str): Razorpay plan ID.

        Returns:
            dict: Data required to open Razorpay checkout.
        """
        try:
            subscription = self.razorpayClient.subscription.create({
                "plan_id": planId,
                "customer_notify": 1,
                "total_count": 12
            })
            return {
                "subscriptionId": subscription["id"],
                "status": subscription["status"]
            }
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def verifySubscription(self, payload: dict) -> None:
        """
        Verify Razorpay subscription checkout signature.

        This method performs official HMAC SHA256 verification
        using Razorpay API secret. Intended for MVP usage
        without webhook integration.

        Args:
            payload (dict): Razorpay checkout response payload.
        """
        try:
            paymentId = payload.get("razorpayPaymentId")
            subscriptionId = payload.get("razorpaySubscriptionId")
            signature = payload.get("razorpaySignature")
            userId = payload.get("userId")
            if not all([paymentId, subscriptionId, signature, userId]):
                raise Exception("Missing Razorpay verification fields")
            message = f"{paymentId}|{subscriptionId}"
            expectedSignature = hmac.new(
                os.environ["RAZORPAY_KEY_SECRET"].encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expectedSignature, signature):
                raise Exception("Invalid Razorpay signature")
            currentTime = datetime.datetime.utcnow()
            expiry = currentTime + datetime.timedelta(days=30)
            self.client.table("Users").update({
                "razorpaySubscriptionId": subscriptionId,
                "subscriptionStatus": "active",
                "subscriptionPlan": "paid",
                "subscriptionStart": currentTime,
                "subscriptionExpiry": expiry
            }).eq("userId", userId).execute()
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception


subscriptionService = SubscriptionService()