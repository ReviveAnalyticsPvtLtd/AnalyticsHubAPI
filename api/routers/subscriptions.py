"""
API router for data subscription operations.

This module provides endpoints for creating data blends, retrieving data sources,
and fetching fields from sources.
"""
__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from utils.exceptionHandler import CustomException, raiseHttpException
from api.models import VerifySubscriptionRequest, CreateSubscriptionRequest, CancelSubscriptionRequest, RefundRequest
from api.services.subscriptionService import subscriptionService
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
from api.commons import verifyToken

router = APIRouter()
"""
Router for subscription-related endpoints.
"""


@router.get("/activateFreeTrial")
async def activateFreeTrial(token=Depends(verifyToken)):
    """
    Activate a free trial for a user.

    Args:
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        subscriptionService.activateFreeTrial(token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Free trial activated successfully."
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/createSubscription")
async def createSubscription(request: CreateSubscriptionRequest, token=Depends(verifyToken)):
    """
    Create a Razorpay subscription for the given domains.

    Args:
        request (CreateSubscriptionRequest): Domains to subscribe.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Subscription details required for checkout.
    """
    try:
        result = subscriptionService.createSubscription(domains=request.domains, token=token)
        return ORJSONResponse(status_code=200, content=result)
    except CustomException as e:
        raiseHttpException(e)


@router.post("/verifySubscription")
async def verifySubscription(
    payload: VerifySubscriptionRequest,
    token=Depends(verifyToken)
):
    """
    Verify Razorpay subscription checkout signature.

    Args:
        payload (VerifySubscriptionRequest): Razorpay checkout response payload.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Verification result.
    """
    try:
        subscriptionService.verifySubscription(payload=payload.dict())
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Subscription verified successfully."
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/cancelSubscription")
async def cancelSubscription(
    payload: CancelSubscriptionRequest,
    token=Depends(verifyToken)
):
    """
    Cancel the authenticated user's Razorpay subscription.

    Args:
        payload (CancelSubscriptionRequest): Cancellation options.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Cancellation result.
    """
    try:
        result = subscriptionService.cancelSubscription(
            token=token,
            cancelAtCycleEnd=payload.cancelAtCycleEnd
        )
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Subscription cancelled successfully.",
                "data": result
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/refund")
async def refund(payload: RefundRequest, token=Depends(verifyToken)):
    """
    Initiate a refund for a Razorpay payment.

    Args:
        payload (RefundRequest): Refund details including payment ID and optional amount.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Refund initiation result.
    """
    try:
        result = subscriptionService.initiateRefund(
            token=token,
            paymentId=payload.paymentId,
            amount=payload.amount
        )
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Refund initiated successfully.",
                "data": result
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.get("/invoices")
async def getInvoices(token=Depends(verifyToken)):
    """
    Retrieve all invoices for the authenticated user.

    Args:
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of invoices.
    """
    try:
        result = subscriptionService.getInvoices(token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "invoices": result
            }
        )
    except CustomException as e:
        raiseHttpException(e)