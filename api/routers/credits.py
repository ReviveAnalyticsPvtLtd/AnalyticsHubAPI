"""
credits.py

Router for credit balance and usage endpoints.
Exposes user-facing read operations for their LLM credit state.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["router"]


from api.commons import verifyUser, UserContext
from utils.exceptionHandler import CustomException, raiseHttpException
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
from utils.logger import logger


router = APIRouter()


@router.get("/balance")
async def getCreditBalance(user: UserContext = Depends(verifyUser)):
    """
    Return the current credit balance for the authenticated user.

    Returns:
        ORJSONResponse: Balance details including remaining/used tokens, the
            monthly token quota, the same figures as derived credits, period
            start/end, and plan tier.
    """
    try:
        from api.services.credits.creditService import creditService

        snapshot = creditService.getBalanceSnapshot(user.userId)
        return ORJSONResponse(
            status_code=200,
            content={"status": "SUCCESS", "data": snapshot},
        )
    except CustomException:
        raise
    except Exception as e:
        exception = CustomException(e)
        logger.error(exception)
        raiseHttpException(exception)


@router.get("/usage")
async def getCreditUsage(user: UserContext = Depends(verifyUser)):
    """
    Return a summary of recent token usage for the authenticated user.

    Includes Supabase-backed token totals (with credits derived at the ratio in
    config/credits.json) and, when available, a per-operation and per-model
    breakdown from the Langfuse Metrics API.

    Returns:
        ORJSONResponse: Usage summary with token and credit totals and optional
            Langfuse breakdown.
    """
    try:
        from api.services.credits.creditService import creditService
        from api.services.credits.langfuseUsageService import getUsageBreakdown
        from datetime import datetime, timezone

        snapshot = creditService.getBalanceSnapshot(user.userId)

        breakdown = None
        langfuseAvailable = False
        periodStart = snapshot.get("periodStart")
        periodEnd = snapshot.get("periodEnd")

        if snapshot.get("initialized") and periodStart and periodEnd:
            breakdown = getUsageBreakdown(
                userId=user.userId,
                fromTimestamp=periodStart,
                toTimestamp=periodEnd,
            )

        if breakdown is None:
            now = datetime.now(timezone.utc)
            monthStart = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            breakdown = getUsageBreakdown(
                userId=user.userId,
                fromTimestamp=monthStart.isoformat(),
                toTimestamp=now.isoformat(),
            )

        if breakdown is not None:
            langfuseAvailable = breakdown.get("langfuseAvailable", False)

        return ORJSONResponse(
            status_code=200,
            content={
                "status": "SUCCESS",
                "data": {
                    "totalUsedTokens": snapshot.get("usedTokens", 0),
                    "monthlyTokenQuota": snapshot.get("monthlyTokenQuota", 0),
                    "remainingTokens": snapshot.get("remainingTokens", 0),
                    "totalUsedCredits": snapshot.get("usedCredits", 0.0),
                    "monthlyCredits": snapshot.get("monthlyCredits", 0.0),
                    "remainingCredits": snapshot.get("remainingCredits", 0.0),
                    "usagePercentage": snapshot.get("usagePercentage", 0.0),
                    "periodStart": periodStart,
                    "periodEnd": periodEnd,
                    "planTier": snapshot.get("planTier", "none"),
                    "langfuseAvailable": langfuseAvailable,
                    "breakdown": {
                        "byOperation": breakdown.get("byOperation", []) if breakdown else [],
                        "byModel": breakdown.get("byModel", []) if breakdown else [],
                    },
                },
            },
        )
    except CustomException:
        raise
    except Exception as e:
        exception = CustomException(e)
        logger.error(exception)
        raiseHttpException(exception)
