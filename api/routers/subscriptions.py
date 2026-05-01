"""
API router for data subscription operations.

This module provides endpoints for creating data blends, retrieving data sources,
and fetching fields from sources.
"""
__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from utils.exceptionHandler import CustomException, raiseHttpException
from api.models import VerifySubscriptionRequest, CreateSubscriptionRequest, AddDomainsRequest, VerifyDomainUpgradeRequest, RemoveDomainRequest, CancelPendingAdditionRequest, CancelSubscriptionRequest, RefundRequest
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
        ORJSONResponse: Success message with affected subscription fields.
    """
    try:
        result = subscriptionService.activateFreeTrial(token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Free trial activated successfully.",
                "data": result
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/createSubscription")
async def createSubscription(request: CreateSubscriptionRequest, token=Depends(verifyToken)):
    """
    Create a Razorpay order with tokenization for the given domains.

    Args:
        request (CreateSubscriptionRequest): Domains to subscribe.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Subscription details required for checkout.
    """
    try:
        result = subscriptionService.createSubscription(domains=request.domains, contact=request.contact, token=token)
        return ORJSONResponse(status_code=200, content=result)
    except CustomException as e:
        raiseHttpException(e)


@router.post("/verifySubscription")
async def verifySubscription(
    payload: VerifySubscriptionRequest,
    token=Depends(verifyToken)
):
    """
    Verify Razorpay order checkout signature and activate token.

    Args:
        payload (VerifySubscriptionRequest): Razorpay checkout response payload.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Verification result.
    """
    try:
        subscriptionService.verifySubscription(payload=payload.dict(), token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Subscription verified successfully."
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/addDomains")
async def addDomains(payload: AddDomainsRequest, token=Depends(verifyToken)):
    """
    Add one or more domains to the authenticated user's subscription
    via a Razorpay Order for prorated billing.

    Args:
        payload (AddDomainsRequest): Domains to add.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Order details required for embedded checkout.
    """
    try:
        result = subscriptionService.addDomains(domains=payload.domains, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Domain upgrade order created.",
                "data": result
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/verifyDomainUpgrade")
async def verifyDomainUpgrade(
    payload: VerifyDomainUpgradeRequest,
    token=Depends(verifyToken)
):
    """
    Verify Razorpay Order checkout signature and activate added domains.

    Args:
        payload (VerifyDomainUpgradeRequest): Razorpay checkout response payload.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Verification result.
    """
    try:
        subscriptionService.verifyDomainUpgrade(payload=payload.dict(), token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Domain upgrade verified and activated."
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/removeDomain")
async def removeDomain(payload: RemoveDomainRequest, token=Depends(verifyToken)):
    """
    Schedule a domain removal at the end of the current billing cycle.

    Args:
        payload (RemoveDomainRequest): Domain to remove.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Current domains, pending removals, and effective timing.
    """
    try:
        result = subscriptionService.removeDomain(domains=payload.domains, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": f"{len(payload.domains)} domain(s) scheduled for removal at cycle end.",
                "data": result
            }
        )
    except CustomException as e:
        raiseHttpException(e)


@router.post("/cancelPendingAddition")
async def cancelPendingAddition(payload: CancelPendingAdditionRequest, token=Depends(verifyToken)):
    """
    Cancel a pending domain addition request.

    Args:
        payload (CancelPendingAdditionRequest): Domain to cancel.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Cancellation result.
    """
    try:
        result = subscriptionService.cancelPendingAddition(domain=payload.domain, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Pending domain addition cancelled.",
                "data": result
            }
        )
    except CustomException as e:
        raiseHttpException(e)

@router.post("/cancelSubscription")
async def cancelSubscription(payload: CancelSubscriptionRequest, token=Depends(verifyToken)):
    """
    Cancel the authenticated user's subscription at the end
    of the current billing cycle.

    Args:
        payload (CancelSubscriptionRequest): Cancellation reason.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Cancellation result.
    """
    try:
        result = subscriptionService.cancelSubscription(reason=payload.reason, token=token)
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "message": "Subscription will be cancelled at the end of the current billing cycle.",
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