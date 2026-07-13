"""
creditService.py

Core credit management for the two-bucket (daily + monthly) system.

Redis holds a per-user HASH at credits:v2:{userId} with fields
mrem, mquota, dcap, dused, dstamp, pend. Two Lua scripts perform the atomic
hot-path operations (peek + deduct) applying the daily reset inline. The
infrequent monthly roll is orchestrated in Python. Supabase credit_balances is
the durable source of truth, rebuilt into Redis on cache miss.
"""

__version__ = "2.0.0"
__author__ = "Rohit Mishra"
__all__ = ["CreditService", "creditService"]


from api.services.credits.creditConfig import (
    TOKEN_TO_CREDIT_RATIO,
    getQuotaForPlan,
    getDailyCapForPlan,
)
from api.services.credits import creditMath
from datetime import datetime, timezone
from dateutil import parser as dateparser
from utils.logger import logger
import redis
import math
import os


# Applies the daily reset inline, returns {effective, mrem, dused}.
# KEYS[1]=hash, ARGV[1]=todayStamp. Returns {-1,-1,-1} when the hash is absent.
_PEEK_LUA = """
local h = redis.call('HGETALL', KEYS[1])
if #h == 0 then return {-1, -1, -1} end
local m = {}
for i = 1, #h, 2 do m[h[i]] = h[i+1] end
local mrem = tonumber(m['mrem'])
local dcap = tonumber(m['dcap'])
local dused = tonumber(m['dused'])
local dstamp = tonumber(m['dstamp'])
local today = tonumber(ARGV[1])
if dstamp ~= today then
  dused = 0
  redis.call('HSET', KEYS[1], 'dused', 0, 'dstamp', today)
end
local dr = dcap - dused
if dr < 0 then dr = 0 end
local eff = mrem
if dr < eff then eff = dr end
if eff < 0 then eff = 0 end
return {eff, mrem, dused}
"""

# Applies daily reset, then decrements both buckets by n (clamped).
# KEYS[1]=hash, ARGV[1]=n, ARGV[2]=todayStamp. Returns {-1,-1,-1} when absent.
_DEDUCT_LUA = """
local h = redis.call('HGETALL', KEYS[1])
if #h == 0 then return {-1, -1, -1} end
local m = {}
for i = 1, #h, 2 do m[h[i]] = h[i+1] end
local mrem = tonumber(m['mrem'])
local dcap = tonumber(m['dcap'])
local dused = tonumber(m['dused'])
local dstamp = tonumber(m['dstamp'])
local n = tonumber(ARGV[1])
local today = tonumber(ARGV[2])
if dstamp ~= today then dused = 0; dstamp = today end
mrem = mrem - n
if mrem < 0 then mrem = 0 end
dused = dused + n
if dused > dcap then dused = dcap end
redis.call('HSET', KEYS[1], 'mrem', mrem, 'dused', dused, 'dstamp', dstamp)
local dr = dcap - dused
if dr < 0 then dr = 0 end
local eff = mrem
if dr < eff then eff = dr end
if eff < 0 then eff = 0 end
return {eff, mrem, dused}
"""


class CreditService:
    """Per-user two-bucket credit balances backed by Redis + Supabase."""

    def __init__(self):
        self._supabase = None

    @property
    def supabase(self):
        """Lazily acquire the shared Supabase client (avoids import-time coupling)."""
        if self._supabase is None:
            from api.commons import client
            self._supabase = client
        return self._supabase

    @supabase.setter
    def supabase(self, value):
        self._supabase = value

    def _redis(self) -> redis.Redis:
        return redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            password=os.environ["REDIS_PASSWORD"],
        )

    @staticmethod
    def _redisKey(userId: str) -> str:
        return f"credits:v2:{userId}"

    # ---- durable read helpers -------------------------------------------------

    def _dbRow(self, userId: str) -> dict | None:
        row = (
            self.supabase.table("credit_balances")
            .select(
                "plan_tier, monthly_quota, used_credits, remaining_credits, "
                "daily_quota, daily_used, daily_stamp, "
                "period_start, period_end, last_reset_at"
            )
            .eq("user_id", userId)
            .limit(1)
            .execute()
        )
        return row.data[0] if row.data else None

    @staticmethod
    def _stampFromDate(value) -> int:
        if not value:
            return 0
        try:
            d = dateparser.parse(str(value))
            return d.year * 10000 + d.month * 100 + d.day
        except Exception:
            return 0

    # ---- Redis hash lifecycle -------------------------------------------------

    def _ensureHash(self, userId: str) -> dict | None:
        """
        Guarantee the Redis hash exists and reflects the current billing period.

        Rebuilds from the DB on cache miss (applying monthly roll + daily reset
        from stored timestamps), and rolls the monthly window in Python when
        period_end has passed. Returns the DB row used, or None if no row.
        """
        now = datetime.now(timezone.utc)
        try:
            r = self._redis()
            key = self._redisKey(userId)
            pend = r.hget(key, "pend")
        except Exception as e:
            logger.warning(f"Redis unavailable in _ensureHash for {userId}: {e}")
            pend = None
            r = None

        needsRebuild = pend is None

        # Monthly roll check (Python) — cheap HGET of period end epoch.
        if not needsRebuild:
            try:
                if now.timestamp() >= float(pend):
                    self._rollMonthly(userId, now)
            except Exception as e:
                logger.warning(f"Monthly roll check failed for {userId}: {e}")
            return None  # hash already current; caller uses Lua

        # Rebuild path.
        dbRow = self._dbRow(userId)
        if not dbRow:
            return None

        mquota = dbRow.get("monthly_quota", 0)
        mrem = dbRow.get("remaining_credits", 0)
        dcap = dbRow.get("daily_quota", 0)
        dused = dbRow.get("daily_used", 0)
        dstamp = self._stampFromDate(dbRow.get("daily_stamp"))
        periodEnd = dbRow.get("period_end")

        # Apply monthly roll from DB timestamps.
        if periodEnd:
            pe = dateparser.parse(periodEnd)
            if pe <= now:
                ps2, pe2 = creditMath.rollMonthly(pe, now)
                mrem = mquota
                self._writePeriod(userId, ps2, pe2, mquota, now)
                periodEnd = pe2.isoformat()

        # Apply daily reset from DB stamp.
        todayStamp = creditMath.dayStamp(now)
        dused, dstamp = creditMath.applyDailyReset(dused, dstamp, todayStamp)

        pendEpoch = int(dateparser.parse(periodEnd).timestamp()) if periodEnd else int(now.timestamp())
        if r is not None:
            try:
                r.hset(self._redisKey(userId), mapping={
                    "mrem": mrem, "mquota": mquota, "dcap": dcap,
                    "dused": dused, "dstamp": dstamp, "pend": pendEpoch,
                })
            except Exception as e:
                logger.warning(f"Redis hash rebuild failed for {userId}: {e}")
        return dbRow

    def _rollMonthly(self, userId: str, now: datetime) -> None:
        """Roll the billing window forward and reset the monthly pool (durable + Redis)."""
        dbRow = self._dbRow(userId)
        if not dbRow or not dbRow.get("period_end"):
            return
        pe = dateparser.parse(dbRow["period_end"])
        if pe > now:
            return
        ps2, pe2 = creditMath.rollMonthly(pe, now)
        mquota = dbRow.get("monthly_quota", 0)
        self._writePeriod(userId, ps2, pe2, mquota, now)
        try:
            r = self._redis()
            r.hset(self._redisKey(userId), mapping={
                "mrem": mquota, "pend": int(pe2.timestamp()),
            })
        except Exception as e:
            logger.warning(f"Redis monthly roll write failed for {userId}: {e}")

    def _writePeriod(self, userId, periodStart, periodEnd, mquota, now) -> None:
        try:
            self.supabase.table("credit_balances").update({
                "used_credits": 0,
                "remaining_credits": mquota,
                "period_start": periodStart.isoformat(),
                "period_end": periodEnd.isoformat(),
                "last_reset_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"DB period write failed for {userId}: {e}")

    # ---- Lua seams (patched in unit tests) ------------------------------------

    def _peek(self, userId: str) -> dict | None:
        """Atomic daily-reset + effective read. Returns {effective,mrem,dused} or None."""
        try:
            r = self._redis()
            today = creditMath.dayStamp(datetime.now(timezone.utc))
            res = r.eval(_PEEK_LUA, 1, self._redisKey(userId), today)
            if not res or int(res[0]) == -1:
                return None
            return {"effective": int(res[0]), "mrem": int(res[1]), "dused": int(res[2])}
        except Exception as e:
            logger.warning(f"Redis peek failed for {userId}: {e}")
            return None

    def _deduct(self, userId: str, n: int) -> dict | None:
        """Atomic daily-reset + two-bucket decrement. Returns {effective,mrem,dused} or None."""
        try:
            r = self._redis()
            today = creditMath.dayStamp(datetime.now(timezone.utc))
            res = r.eval(_DEDUCT_LUA, 1, self._redisKey(userId), n, today)
            if not res or int(res[0]) == -1:
                return None
            return {"effective": int(res[0]), "mrem": int(res[1]), "dused": int(res[2])}
        except Exception as e:
            logger.warning(f"Redis deduct failed for {userId}: {e}")
            return None

    # ---- public API -----------------------------------------------------------

    def initializeCreditBalance(self, userId, planTier, subscriptionId=None) -> dict:
        """Create or reset a user's balance on activation / trial start / renewal."""
        from dateutil.relativedelta import relativedelta

        quota = getQuotaForPlan(planTier)
        dcap = getDailyCapForPlan(planTier)
        now = datetime.now(timezone.utc)
        periodEnd = now + relativedelta(months=1)
        todayStamp = creditMath.dayStamp(now)

        payload = {
            "user_id": userId,
            "subscription_id": str(subscriptionId) if subscriptionId else None,
            "plan_tier": planTier,
            "monthly_quota": quota,
            "used_credits": 0,
            "remaining_credits": quota,
            "daily_quota": dcap,
            "daily_used": 0,
            "daily_stamp": now.date().isoformat(),
            "daily_reset_at": now.isoformat(),
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
            r.hset(self._redisKey(userId), mapping={
                "mrem": quota, "mquota": quota, "dcap": dcap,
                "dused": 0, "dstamp": todayStamp, "pend": int(periodEnd.timestamp()),
            })
            logger.info(
                f"Credit balance initialized — userId={userId}, plan={planTier}, "
                f"quota={quota}, dailyCap={dcap}"
            )
        except Exception as e:
            logger.warning(f"Redis credit init failed for {userId}: {e}")
        return result.data[0] if result.data else payload

    def deductCredits(self, userId, tokensUsed, operationType) -> int:
        """Convert tokens to credits and atomically deduct from both buckets."""
        creditsToDeduct = max(1, math.ceil(tokensUsed / TOKEN_TO_CREDIT_RATIO))

        self._ensureHash(userId)
        state = self._deduct(userId, creditsToDeduct)
        if state is None:
            # Redis miss — rebuild then retry once.
            self._ensureHash(userId)
            state = self._deduct(userId, creditsToDeduct)
        if state is None:
            logger.warning(f"Credit deduction unavailable for {userId}")
            return -1

        logger.info(
            f"Credit deducted — userId={userId}, tokens={tokensUsed}, "
            f"credits={creditsToDeduct}, monthlyRemaining={state['mrem']}, "
            f"dailyUsed={state['dused']}, effective={state['effective']}, op={operationType}"
        )

        # Durable write-back of both buckets.
        try:
            now = datetime.now(timezone.utc)
            mquota = 0
            row = self._dbRow(userId)
            if row:
                mquota = row.get("monthly_quota", 0)
            self.supabase.table("credit_balances").update({
                "remaining_credits": state["mrem"],
                "used_credits": max(0, mquota - state["mrem"]),
                "daily_used": state["dused"],
                "daily_stamp": now.date().isoformat(),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"DB credit write-back failed for {userId}: {e}")

        return state["effective"]

    def getRemainingCredits(self, userId: str) -> int:
        """Effective spendable remaining = min(monthly, daily). -1 on total failure."""
        self._ensureHash(userId)
        state = self._peek(userId)
        if state is not None:
            return state["effective"]

        # DB fallback (Redis down / cold) — apply lazy resets in Python.
        try:
            row = self._dbRow(userId)
            if not row:
                return -1
            now = datetime.now(timezone.utc)
            mrem = row.get("remaining_credits", 0)
            dcap = row.get("daily_quota", 0)
            dused = row.get("daily_used", 0)
            dstamp = self._stampFromDate(row.get("daily_stamp"))
            dused, _ = creditMath.applyDailyReset(dused, dstamp, creditMath.dayStamp(now))
            return creditMath.effectiveRemaining(mrem, dcap, dused)
        except Exception as e:
            logger.warning(f"DB credit read failed for {userId}: {e}")
            return -1

    def resetMonthlyCredits(self, userId: str) -> None:
        """Event-driven monthly reset (e.g. annual renewal). Resets both buckets' periods."""
        from dateutil.relativedelta import relativedelta
        try:
            row = self._dbRow(userId)
            if not row:
                logger.warning(f"No credit_balances row for userId={userId}, skipping reset")
                return
            quota = row.get("monthly_quota", 0)
            now = datetime.now(timezone.utc)
            oldEnd = row.get("period_end")
            periodStart = dateparser.parse(oldEnd) if oldEnd else now
            periodEnd = periodStart + relativedelta(months=1)
            todayStamp = creditMath.dayStamp(now)

            self.supabase.table("credit_balances").update({
                "used_credits": 0,
                "remaining_credits": quota,
                "daily_used": 0,
                "daily_stamp": now.date().isoformat(),
                "daily_reset_at": now.isoformat(),
                "period_start": periodStart.isoformat(),
                "period_end": periodEnd.isoformat(),
                "last_reset_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()

            try:
                r = self._redis()
                r.hset(self._redisKey(userId), mapping={
                    "mrem": quota, "dused": 0, "dstamp": todayStamp,
                    "pend": int(periodEnd.timestamp()),
                })
            except Exception:
                pass
            logger.info(f"Monthly credit reset for userId={userId}, quota={quota}")
        except Exception as e:
            logger.error(f"Credit reset failed for userId={userId}: {e}")

    def reconcile(self, userId: str) -> None:
        """Safety-net sync of the Redis hash back to Supabase (guards eviction/drift)."""
        try:
            state = self._peek(userId)
            row = self._dbRow(userId)
            if not row:
                return
            if state is None:
                # Redis missing — rebuild from DB.
                self._ensureHash(userId)
                return
            mquota = row.get("monthly_quota", 0)
            now = datetime.now(timezone.utc)
            self.supabase.table("credit_balances").update({
                "remaining_credits": state["mrem"],
                "used_credits": max(0, mquota - state["mrem"]),
                "daily_used": state["dused"],
                "daily_stamp": now.date().isoformat(),
                "last_reconciled_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"Credit reconciliation failed for userId={userId}: {e}")

    def getBalanceSnapshot(self, userId: str) -> dict:
        """Login/balance snapshot with both buckets. Never raises."""
        defaults = {
            "planTier": "none", "monthlyQuota": 0, "usedCredits": 0,
            "remainingCredits": 0, "dailyQuota": 0, "dailyUsed": 0,
            "dailyRemaining": 0, "effectiveRemaining": 0, "usagePercentage": 0.0,
            "periodStart": None, "periodEnd": None, "lastResetAt": None,
            "dailyResetAt": None, "initialized": False,
        }
        try:
            row = self._dbRow(userId)
            if not row:
                return defaults

            now = datetime.now(timezone.utc)
            quota = row.get("monthly_quota", 0)
            used = row.get("used_credits", 0)
            dcap = row.get("daily_quota", 0)
            dused = row.get("daily_used", 0)
            dstamp = self._stampFromDate(row.get("daily_stamp"))
            dused, _ = creditMath.applyDailyReset(dused, dstamp, creditMath.dayStamp(now))

            effective = self.getRemainingCredits(userId)
            monthlyRemaining = row.get("remaining_credits", 0)
            if effective == -1:
                effective = creditMath.effectiveRemaining(monthlyRemaining, dcap, dused)
            dailyRemaining = max(0, dcap - dused)
            usagePercent = round((used / quota) * 100, 2) if quota > 0 else 0.0

            return {
                "planTier": row.get("plan_tier", "none"),
                "monthlyQuota": quota,
                "usedCredits": used,
                "remainingCredits": monthlyRemaining,
                "dailyQuota": dcap,
                "dailyUsed": dused,
                "dailyRemaining": dailyRemaining,
                "effectiveRemaining": effective,
                "usagePercentage": usagePercent,
                "periodStart": row.get("period_start"),
                "periodEnd": row.get("period_end"),
                "lastResetAt": row.get("last_reset_at"),
                "dailyResetAt": creditMath.nextUtcMidnight(now).isoformat(),
                "initialized": True,
            }
        except Exception as e:
            logger.warning(f"getBalanceSnapshot failed for userId={userId}: {e}")
            return defaults


creditService = CreditService()
