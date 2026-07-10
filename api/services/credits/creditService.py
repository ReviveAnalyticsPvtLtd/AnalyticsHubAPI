"""
creditService.py

Core credit management service providing initialization, deduction,
balance queries, monthly resets, and Redis-DB reconciliation.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["CreditService", "creditService", "getBalanceSnapshot"]


from api.services.credits.creditConfig import (
    TOKEN_TO_CREDIT_RATIO,
    getQuotaForPlan,
)
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from utils.logger import logger
from api.commons import client
import redis
import math
import os


class CreditService:
    """
    Manages per-user credit balances backed by Redis (real-time) and
    Supabase credit_balances table (durable).
    """

    def __init__(self):
        self.supabase = client

    def _redis(self) -> redis.Redis:
        return redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            password=os.environ["REDIS_PASSWORD"],
        )

    @staticmethod
    def _redisKey(userId: str) -> str:
        return f"credits:{userId}"

    def initializeCreditBalance(
        self,
        userId: str,
        planTier: str,
        subscriptionId: str | None = None,
    ) -> dict:
        """
        Create or reset the credit balance for a user when their
        subscription is activated, trial starts, or period renews.

        Args:
            userId:         The platform user ID.
            planTier:       One of 'free', 'pro', 'annual', 'none'.
            subscriptionId: FK to the subscriptions table (optional).

        Returns:
            dict: The upserted credit_balances row.
        """
        quota = getQuotaForPlan(planTier)
        now = datetime.now(timezone.utc)
        periodEnd = now + relativedelta(months=1)

        payload = {
            "user_id": userId,
            "subscription_id": str(subscriptionId) if subscriptionId else None,
            "plan_tier": planTier,
            "monthly_quota": quota,
            "used_credits": 0,
            "remaining_credits": quota,
            "period_start": now.isoformat(),
            "period_end": periodEnd.isoformat(),
            "last_reset_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        result = (
            self.supabase.table("credit_balances")
            .upsert(payload, on_conflict="user_id")
            .execute()
        )

        try:
            r = self._redis()
            r.set(self._redisKey(userId), quota)
            logger.info(
                f"Credit balance initialized — userId={userId}, "
                f"planTier={planTier}, quota={quota}"
            )
        except Exception as e:
            logger.warning(f"Redis credit init failed for {userId}: {e}")

        return result.data[0] if result.data else payload

    def deductCredits(
        self,
        userId: str,
        tokensUsed: int,
        operationType: str,
    ) -> int:
        """
        Convert tokens to credits and atomically deduct from the user's
        balance in Redis, then async-write the updated totals to Supabase.

        Args:
            userId:        The platform user ID.
            tokensUsed:    Total tokens consumed (prompt + completion).
            operationType: The operation label for logging.

        Returns:
            int: Remaining credits after deduction.
        """
        creditsToDeduct = max(1, math.ceil(tokensUsed / TOKEN_TO_CREDIT_RATIO))

        try:
            r = self._redis()
            key = self._redisKey(userId)

            remaining = r.decrby(key, creditsToDeduct)
            if remaining < 0:
                r.set(key, 0)
                remaining = 0

            logger.info(
                f"Credit deducted — userId={userId}, "
                f"tokens={tokensUsed}, credits={creditsToDeduct}, "
                f"remaining={remaining}, op={operationType}"
            )
        except Exception as e:
            logger.warning(f"Redis credit deduction failed for {userId}: {e}")
            remaining = -1

        try:
            now = datetime.now(timezone.utc).isoformat()
            row = (
                self.supabase.table("credit_balances")
                .select("used_credits, remaining_credits")
                .eq("user_id", userId)
                .limit(1)
                .execute()
            )
            if row.data:
                currentUsed = row.data[0].get("used_credits", 0)
                newUsed = currentUsed + creditsToDeduct
                newRemaining = max(0, row.data[0].get("remaining_credits", 0) - creditsToDeduct)
                self.supabase.table("credit_balances").update({
                    "used_credits": newUsed,
                    "remaining_credits": newRemaining,
                    "updated_at": now,
                }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"DB credit write-back failed for {userId}: {e}")

        return remaining

    def getRemainingCredits(self, userId: str) -> int:
        """
        Read remaining credits from Redis (fast path), falling back to
        Supabase if the key is missing.

        Returns:
            int: Remaining credits (-1 on total failure).
        """
        try:
            r = self._redis()
            cached = r.get(self._redisKey(userId))
            if cached is not None:
                return max(0, int(cached))
        except Exception as e:
            logger.warning(f"Redis credit read failed for {userId}: {e}")

        try:
            row = (
                self.supabase.table("credit_balances")
                .select("remaining_credits")
                .eq("user_id", userId)
                .limit(1)
                .execute()
            )
            if row.data:
                remaining = row.data[0].get("remaining_credits", 0)
                try:
                    r = self._redis()
                    r.set(self._redisKey(userId), remaining)
                except Exception:
                    pass
                return remaining
        except Exception as e:
            logger.warning(f"DB credit read failed for {userId}: {e}")

        return -1

    def resetMonthlyCredits(self, userId: str) -> None:
        """
        Reset a user's credits for a new billing period.

        Called by the Celery credit-reset task when period_end <= NOW().
        """
        try:
            row = (
                self.supabase.table("credit_balances")
                .select("monthly_quota, period_start, period_end")
                .eq("user_id", userId)
                .limit(1)
                .execute()
            )
            if not row.data:
                logger.warning(f"No credit_balances row for userId={userId}, skipping reset")
                return

            record = row.data[0]
            quota = record.get("monthly_quota", 0)
            now = datetime.now(timezone.utc)

            oldEnd = record.get("period_end")
            if oldEnd:
                from dateutil import parser
                periodStart = parser.parse(oldEnd)
            else:
                periodStart = now
            periodEnd = periodStart + relativedelta(months=1)

            self.supabase.table("credit_balances").update({
                "used_credits": 0,
                "remaining_credits": quota,
                "period_start": periodStart.isoformat(),
                "period_end": periodEnd.isoformat(),
                "last_reset_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()

            try:
                r = self._redis()
                r.set(self._redisKey(userId), quota)
            except Exception:
                pass

            logger.info(f"Monthly credit reset for userId={userId}, quota={quota}")
        except Exception as e:
            logger.error(f"Credit reset failed for userId={userId}: {e}")

    def reconcile(self, userId: str) -> None:
        """
        Sync the Redis balance back to Supabase credit_balances.

        Handles the case where the Redis key is missing by re-populating
        from the DB.
        """
        try:
            r = self._redis()
            key = self._redisKey(userId)
            cached = r.get(key)

            row = (
                self.supabase.table("credit_balances")
                .select("remaining_credits, monthly_quota, used_credits")
                .eq("user_id", userId)
                .limit(1)
                .execute()
            )
            if not row.data:
                return

            dbRecord = row.data[0]

            if cached is None:
                r.set(key, dbRecord.get("remaining_credits", 0))
                return

            redisRemaining = max(0, int(cached))
            dbRemaining = dbRecord.get("remaining_credits", 0)

            if redisRemaining != dbRemaining:
                quota = dbRecord.get("monthly_quota", 0)
                newUsed = max(0, quota - redisRemaining)
                self.supabase.table("credit_balances").update({
                    "used_credits": newUsed,
                    "remaining_credits": redisRemaining,
                    "last_reconciled_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"Credit reconciliation failed for userId={userId}: {e}")


    def getBalanceSnapshot(self, userId: str) -> dict:
        """
        Build a consistent credit-balance snapshot suitable for embedding
        in login responses or returning from the balance endpoint.

        Never raises — returns safe defaults on any failure so that
        callers (especially login) are not disrupted.

        Returns:
            dict with keys: planTier, monthlyQuota, usedCredits,
            remainingCredits, usagePercentage, periodStart, periodEnd,
            lastResetAt, initialized.
        """
        defaults = {
            "planTier": "none",
            "monthlyQuota": 0,
            "usedCredits": 0,
            "remainingCredits": 0,
            "usagePercentage": 0.0,
            "periodStart": None,
            "periodEnd": None,
            "lastResetAt": None,
            "initialized": False,
        }

        try:
            row = (
                self.supabase.table("credit_balances")
                .select(
                    "plan_tier, monthly_quota, used_credits, remaining_credits, "
                    "period_start, period_end, last_reset_at"
                )
                .eq("user_id", userId)
                .limit(1)
                .execute()
            )

            if not row.data:
                return defaults

            balance = row.data[0]
            remaining = self.getRemainingCredits(userId)
            effectiveRemaining = remaining if remaining != -1 else balance.get("remaining_credits", 0)
            quota = balance.get("monthly_quota", 0)
            used = balance.get("used_credits", 0)
            usagePercent = round((used / quota) * 100, 2) if quota > 0 else 0.0

            return {
                "planTier": balance.get("plan_tier", "none"),
                "monthlyQuota": quota,
                "usedCredits": used,
                "remainingCredits": effectiveRemaining,
                "usagePercentage": usagePercent,
                "periodStart": balance.get("period_start"),
                "periodEnd": balance.get("period_end"),
                "lastResetAt": balance.get("last_reset_at"),
                "initialized": True,
            }
        except Exception as e:
            logger.warning(f"getBalanceSnapshot failed for userId={userId}: {e}")
            return defaults


creditService = CreditService()
