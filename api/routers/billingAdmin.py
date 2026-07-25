"""
Admin-only billing reconciliation and observability endpoints.

These endpoints expose billing operational actions for support/engineering:
reconciliation reports, webhook replay, investigation notes, and billing
metric snapshots.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["router"]


from api.models import (
    MarkReconciliationInvestigatedRequest,
    ReplayWebhookEventRequest,
)
from api.services.billing.billingMetricsService import BillingMetricsService
from api.services.billing.reconciliationService import ReconciliationService
from utils.exceptionHandler import CustomException, raiseHttpException
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends, HTTPException, status
from api.commons import verifyToken
from utils.logger import logger
from jose import jwt
import os


router = APIRouter()


def verifyBillingAdmin(token=Depends(verifyToken)) -> str:
    """
    Require a valid session token whose JWT userId is explicitly allowlisted.

    Configure `BILLING_ADMIN_USER_IDS` as a comma-separated list of internal
    user IDs. Keeping this allowlist explicit avoids accidentally exposing
    manual billing operations to normal authenticated users.
    """
    allowedUserIds = {
        userId.strip()
        for userId in os.environ.get("BILLING_ADMIN_USER_IDS", "").split(",")
        if userId.strip()
    }
    if not allowedUserIds:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Billing admin access is not configured",
        )
    try:
        decodedToken = jwt.decode(
            token,
            os.environ["SECRET_KEY"],
            algorithms=["HS256"],
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid billing admin token",
        )
    userId = decodedToken.get("userId")
    if userId not in allowedUserIds:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Billing admin access denied",
        )
    return userId


@router.get("/reconciliation/report")
async def getReconciliationReport(_adminUserId=Depends(verifyBillingAdmin)):
    """
    Return stale attempts, provider/internal mismatches, and webhook anomalies.
    """
    try:
        report = ReconciliationService().generateReport()
        return ORJSONResponse(
            status_code=200,
            content={"status": "SUCCESS", "data": report},
        )
    except CustomException as e:
        raiseHttpException(e)
    except Exception as e:
        raiseHttpException(CustomException(e))


@router.get("/metrics")
async def getBillingMetrics(_adminUserId=Depends(verifyBillingAdmin)):
    """
    Return a current billing metrics snapshot and evaluated alert list.
    """
    try:
        service = BillingMetricsService()
        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "data": {
                    "metrics": service.collectMetrics(),
                    "alerts": service.evaluateAlerts(),
                },
            },
        )
    except CustomException as e:
        raiseHttpException(e)
    except Exception as e:
        raiseHttpException(CustomException(e))


@router.post("/reconciliation/webhooks/replay")
async def replayWebhookEvent(
    payload: ReplayWebhookEventRequest,
    adminUserId=Depends(verifyBillingAdmin),
):
    """
    Replay a stored webhook event through the idempotent handler pipeline.
    """
    try:
        result = ReconciliationService().replayWebhookEvent(
            eventId=payload.eventId,
            adminUserId=adminUserId,
        )
        return ORJSONResponse(
            status_code=200,
            content={"status": "SUCCESS", "data": result},
        )
    except CustomException as e:
        raiseHttpException(e)
    except Exception as e:
        raiseHttpException(CustomException(e))


@router.post("/reconciliation/mark-investigated")
async def markInvestigated(
    payload: MarkReconciliationInvestigatedRequest,
    adminUserId=Depends(verifyBillingAdmin),
):
    """
    Mark an anomaly as investigated and persist the operator's audit note.
    """
    try:
        result = ReconciliationService().markInvestigated(
            entityType=payload.entityType,
            entityId=payload.entityId,
            adminUserId=adminUserId,
            note=payload.note,
        )
        return ORJSONResponse(
            status_code=200,
            content={"status": "SUCCESS", "data": result},
        )
    except CustomException as e:
        raiseHttpException(e)
    except Exception as e:
        raiseHttpException(CustomException(e))


@router.post("/credits/force-reset")
async def forceResetAllQuotas(
    resetUsage: bool = False,
    adminUserId=Depends(verifyBillingAdmin),
):
    """
    Recompute monthly_token_quota for all users from credits.json, then flush
    all Redis credit hashes so they rebuild with updated values.

    Query params:
        resetUsage (bool, default false): when true, also zero out used_tokens
            and restore remaining_tokens to the full quota for all users,
            giving everyone a fresh monthly bucket immediately. The billing
            period itself is left untouched.
    """
    try:
        from api.services.credits.creditService import creditService

        result = creditService.forceResetAllQuotas(resetUsage=resetUsage)
        logger.info(
            f"Force credit reset triggered by admin={adminUserId}, "
            f"resetUsage={resetUsage}: {result}"
        )
        return ORJSONResponse(
            status_code=200,
            content={"status": "SUCCESS", "data": result},
        )
    except CustomException as e:
        raiseHttpException(e)
    except Exception as e:
        raiseHttpException(CustomException(e))
