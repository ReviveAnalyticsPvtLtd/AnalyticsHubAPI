"""
creditService.py

Core credit management for the single-bucket monthly token system.

Redis holds a per-user HASH at credits:v3:{userId} with fields trem, tquota,
pend, pnext. Two Lua scripts perform the atomic hot-path operations (peek +
deduct) and apply the monthly roll inline, so the first request after a billing
boundary already sees a full quota. Python then repairs the exact calendar
boundary and persists the new period. Supabase credit_balances is the durable
source of truth, rebuilt into Redis on cache miss.

Tokens are the unit of record everywhere; credits are derived for display only.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["CreditService", "creditService"]


from api.services.credits.creditConfig import (
    TOKEN_TO_CREDIT_RATIO,
    getTokenQuotaForPlan,
)
from api.services.credits import creditMath
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from dateutil import parser as dateparser
from utils.logger import logger
import redis
import os


# Seconds added to pnext by the Lua roll as a safety bound (31 days). Python
# replaces it with the exact calendar value immediately after, but if that
# write never lands the boundary is still monotonic and the loop terminates.
_ROLL_FALLBACK_SECONDS = 2678400

# Upper bound on roll iterations inside Lua (10 years of missed periods).
_ROLL_MAX_ITERATIONS = 120

# Rolls the billing period inline for every boundary `now` has passed, then
# reports the remaining tokens. KEYS[1]=hash, ARGV[1]=nowEpoch,
# ARGV[2]=fallbackSeconds, ARGV[3]=maxIterations.
# Returns {trem, rolled}, or {-1,-1} when the hash is absent.
_PEEK_LUA = """
local h = redis.call('HGETALL', KEYS[1])
if #h == 0 then return {-1, -1} end
local m = {}
for i = 1, #h, 2 do m[h[i]] = h[i+1] end
local trem = tonumber(m['trem'])
local tquota = tonumber(m['tquota'])
local pend = tonumber(m['pend'])
local pnext = tonumber(m['pnext'])
local now = tonumber(ARGV[1])
local fallback = tonumber(ARGV[2])
local maxIter = tonumber(ARGV[3])
local rolled = 0
local guard = 0
while now >= pend and guard < maxIter do
  trem = tquota
  pend = pnext
  pnext = pnext + fallback
  rolled = 1
  guard = guard + 1
end
if trem < 0 then trem = 0 end
if rolled == 1 then
  redis.call('HSET', KEYS[1], 'trem', trem, 'pend', pend, 'pnext', pnext)
end
return {trem, rolled}
"""

# Same roll, then subtracts n tokens from the remaining balance (clamped at 0).
# KEYS[1]=hash, ARGV[1]=n, ARGV[2]=nowEpoch, ARGV[3]=fallbackSeconds,
# ARGV[4]=maxIterations. Returns {trem, rolled}, or {-1,-1} when absent.
_DEDUCT_LUA = """
local h = redis.call('HGETALL', KEYS[1])
if #h == 0 then return {-1, -1} end
local m = {}
for i = 1, #h, 2 do m[h[i]] = h[i+1] end
local trem = tonumber(m['trem'])
local tquota = tonumber(m['tquota'])
local pend = tonumber(m['pend'])
local pnext = tonumber(m['pnext'])
local n = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local fallback = tonumber(ARGV[3])
local maxIter = tonumber(ARGV[4])
local rolled = 0
local guard = 0
while now >= pend and guard < maxIter do
  trem = tquota
  pend = pnext
  pnext = pnext + fallback
  rolled = 1
  guard = guard + 1
end
trem = trem - n
if trem < 0 then trem = 0 end
redis.call('HSET', KEYS[1], 'trem', trem, 'pend', pend, 'pnext', pnext)
return {trem, rolled}
"""

_redis_pool: redis.ConnectionPool | None = None


class CreditService:
    """Per-user monthly token balances backed by Redis + Supabase."""

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
        global _redis_pool
        if _redis_pool is None:
            _redis_pool = redis.ConnectionPool(
                host=os.environ["REDIS_HOST"],
                port=int(os.environ["REDIS_PORT"]),
                password=os.environ["REDIS_PASSWORD"],
            )
        return redis.Redis(connection_pool=_redis_pool)

    @staticmethod
    def _redisKey(userId: str) -> str:
        return f"credits:v3:{userId}"

    # ---- durable read helpers -------------------------------------------------

    def _dbRow(self, userId: str) -> dict | None:
        row = (
            self.supabase.table("credit_balances")
            .select(
                "plan_tier, monthly_token_quota, used_tokens, remaining_tokens, "
                "period_start, period_end, last_reset_at"
            )
            .eq("user_id", userId)
            .limit(1)
            .execute()
        )
        return row.data[0] if row.data else None

    # ---- Redis hash lifecycle -------------------------------------------------

    def _ensureHash(self, userId: str) -> dict | None:
        """
        Guarantee the Redis hash exists for the user.

        Rebuilds from the DB on cache miss, applying the monthly roll from the
        stored period_end so an evicted key never resurrects a stale balance.
        Boundary crossings on a live hash are handled by the Lua roll, not here.
        Returns the DB row used, or None when the hash is already present or no
        row exists.
        """
        now = datetime.now(timezone.utc)
        try:
            r = self._redis()
            if r.hget(self._redisKey(userId), "pend") is not None:
                return None
        except Exception as e:
            logger.warning(f"Redis unavailable in _ensureHash for {userId}: {e}")
            r = None

        dbRow = self._dbRow(userId)
        if not dbRow:
            return None

        tquota = dbRow.get("monthly_token_quota", 0)
        trem = dbRow.get("remaining_tokens", 0)
        periodEnd = dbRow.get("period_end")

        pe = dateparser.parse(periodEnd) if periodEnd else now
        if pe <= now:
            ps2, pe2 = creditMath.rollMonthly(pe, now)
            trem = tquota
            self._writePeriod(userId, ps2, pe2, tquota, now)
            pe = pe2

        if r is not None:
            try:
                r.hset(self._redisKey(userId), mapping={
                    "trem": trem,
                    "tquota": tquota,
                    "pend": int(pe.timestamp()),
                    "pnext": int(creditMath.nextPeriodEnd(pe).timestamp()),
                })
            except Exception as e:
                logger.warning(f"Redis hash rebuild failed for {userId}: {e}")
        return dbRow

    def _repairPeriod(self, userId: str, now: datetime) -> None:
        """
        Follow-up to a Lua roll: replace the 31-day safety bound on `pnext` with
        the exact calendar boundary and persist the new period to Supabase.

        Best-effort. `pend` has already advanced inside Redis, so a failure here
        cannot cause a double roll — the next request repairs it instead.
        """
        try:
            r = self._redis()
            key = self._redisKey(userId)
            pendRaw, quotaRaw = r.hmget(key, "pend", "tquota")
            if pendRaw is None:
                return
            periodEnd = datetime.fromtimestamp(float(pendRaw), tz=timezone.utc)
            r.hset(key, "pnext", int(creditMath.nextPeriodEnd(periodEnd).timestamp()))
            tquota = int(quotaRaw) if quotaRaw is not None else 0
        except Exception as e:
            logger.warning(f"Redis period repair failed for {userId}: {e}")
            return

        try:
            row = self._dbRow(userId)
            storedEnd = dateparser.parse(row["period_end"]) if row and row.get("period_end") else None
            # Another request already persisted this roll — nothing to write.
            if storedEnd is not None and storedEnd >= periodEnd:
                return
            periodStart = storedEnd if storedEnd is not None else periodEnd - relativedelta(months=1)
            self._writePeriod(userId, periodStart, periodEnd, tquota, now)
            logger.info(
                f"Monthly token quota rolled — userId={userId}, quota={tquota}, "
                f"periodEnd={periodEnd.isoformat()}"
            )
        except Exception as e:
            logger.warning(f"DB period repair failed for {userId}: {e}")

    def _writePeriod(self, userId, periodStart, periodEnd, tquota, now) -> None:
        try:
            self.supabase.table("credit_balances").update({
                "used_tokens": 0,
                "remaining_tokens": tquota,
                "period_start": periodStart.isoformat(),
                "period_end": periodEnd.isoformat(),
                "last_reset_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"DB period write failed for {userId}: {e}")

    # ---- Lua seams (patched in unit tests) ------------------------------------

    def _peek(self, userId: str) -> dict | None:
        """Atomic monthly roll + remaining read. Returns {trem,rolled} or None."""
        try:
            r = self._redis()
            now = int(datetime.now(timezone.utc).timestamp())
            res = r.eval(
                _PEEK_LUA, 1, self._redisKey(userId),
                now, _ROLL_FALLBACK_SECONDS, _ROLL_MAX_ITERATIONS,
            )
            if not res or int(res[0]) == -1:
                return None
            return {"trem": int(res[0]), "rolled": int(res[1])}
        except Exception as e:
            logger.warning(f"Redis peek failed for {userId}: {e}")
            return None

    def _deduct(self, userId: str, n: int) -> dict | None:
        """Atomic monthly roll + token decrement. Returns {trem,rolled} or None."""
        try:
            r = self._redis()
            now = int(datetime.now(timezone.utc).timestamp())
            res = r.eval(
                _DEDUCT_LUA, 1, self._redisKey(userId),
                n, now, _ROLL_FALLBACK_SECONDS, _ROLL_MAX_ITERATIONS,
            )
            if not res or int(res[0]) == -1:
                return None
            return {"trem": int(res[0]), "rolled": int(res[1])}
        except Exception as e:
            logger.warning(f"Redis deduct failed for {userId}: {e}")
            return None

    # ---- public API -----------------------------------------------------------

    def initializeCreditBalance(self, userId, planTier, subscriptionId=None) -> dict:
        """Create or reset a user's balance on activation / trial start / renewal."""
        quota = getTokenQuotaForPlan(planTier)
        now = datetime.now(timezone.utc)
        periodEnd = now + relativedelta(months=1)

        payload = {
            "user_id": userId,
            "subscription_id": str(subscriptionId) if subscriptionId else None,
            "plan_tier": planTier,
            "monthly_token_quota": quota,
            "used_tokens": 0,
            "remaining_tokens": quota,
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
                "trem": quota,
                "tquota": quota,
                "pend": int(periodEnd.timestamp()),
                "pnext": int(creditMath.nextPeriodEnd(periodEnd).timestamp()),
            })
            logger.info(
                f"Credit balance initialized — userId={userId}, plan={planTier}, "
                f"monthlyTokens={quota}"
            )
        except Exception as e:
            logger.warning(f"Redis credit init failed for {userId}: {e}")
        return result.data[0] if result.data else payload

    def deductTokens(self, userId, tokensUsed, operationType) -> int:
        """
        Subtract the exact token count from the user's monthly balance.

        No rounding: the tokens reported by the LLM response are the tokens
        charged. Returns the remaining tokens, or -1 when the balance is
        unreachable (the call is then not charged).
        """
        if tokensUsed <= 0:
            return self.getRemainingTokens(userId)

        self._ensureHash(userId)
        state = self._deduct(userId, tokensUsed)
        if state is None:
            # Redis miss — rebuild then retry once.
            self._ensureHash(userId)
            state = self._deduct(userId, tokensUsed)
        if state is None:
            logger.warning(f"Token deduction unavailable for {userId}")
            return -1

        now = datetime.now(timezone.utc)
        if state["rolled"]:
            self._repairPeriod(userId, now)

        logger.info(
            f"Tokens deducted — userId={userId}, tokens={tokensUsed}, "
            f"remaining={state['trem']}, op={operationType}"
        )

        # Durable write-back.
        try:
            row = self._dbRow(userId)
            tquota = row.get("monthly_token_quota", 0) if row else 0
            self.supabase.table("credit_balances").update({
                "remaining_tokens": state["trem"],
                "used_tokens": max(0, tquota - state["trem"]),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"DB token write-back failed for {userId}: {e}")

        return state["trem"]

    def getRemainingTokens(self, userId: str) -> int:
        """Remaining tokens in the current billing period. -1 on total failure."""
        self._ensureHash(userId)
        state = self._peek(userId)
        if state is not None:
            if state["rolled"]:
                self._repairPeriod(userId, datetime.now(timezone.utc))
            return state["trem"]

        # DB fallback (Redis down / cold) — apply the lazy roll in Python.
        try:
            row = self._dbRow(userId)
            if not row:
                return -1
            now = datetime.now(timezone.utc)
            periodEnd = row.get("period_end")
            if periodEnd and dateparser.parse(periodEnd) <= now:
                return row.get("monthly_token_quota", 0)
            return row.get("remaining_tokens", 0)
        except Exception as e:
            logger.warning(f"DB token read failed for {userId}: {e}")
            return -1

    def resetMonthlyTokens(self, userId: str) -> None:
        """Event-driven monthly reset (e.g. annual renewal). Restores the full quota."""
        try:
            row = self._dbRow(userId)
            if not row:
                logger.warning(f"No credit_balances row for userId={userId}, skipping reset")
                return
            quota = row.get("monthly_token_quota", 0)
            now = datetime.now(timezone.utc)
            oldEnd = row.get("period_end")
            periodStart = dateparser.parse(oldEnd) if oldEnd else now
            periodEnd = creditMath.nextPeriodEnd(periodStart)

            self._writePeriod(userId, periodStart, periodEnd, quota, now)

            try:
                r = self._redis()
                r.hset(self._redisKey(userId), mapping={
                    "trem": quota,
                    "tquota": quota,
                    "pend": int(periodEnd.timestamp()),
                    "pnext": int(creditMath.nextPeriodEnd(periodEnd).timestamp()),
                })
            except Exception:
                pass
            logger.info(f"Monthly token reset for userId={userId}, quota={quota}")
        except Exception as e:
            logger.error(f"Token reset failed for userId={userId}: {e}")

    def reconcile(self, userId: str) -> None:
        """Safety-net sync of the Redis hash back to Supabase (guards eviction/drift)."""
        try:
            self.syncQuotaFromConfig(userId)

            state = self._peek(userId)
            row = self._dbRow(userId)
            if not row:
                return
            if state is None:
                self._ensureHash(userId)
                return
            if state["rolled"]:
                self._repairPeriod(userId, datetime.now(timezone.utc))
            tquota = row.get("monthly_token_quota", 0)
            now = datetime.now(timezone.utc)
            self.supabase.table("credit_balances").update({
                "remaining_tokens": state["trem"],
                "used_tokens": max(0, tquota - state["trem"]),
                "last_reconciled_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("user_id", userId).execute()
        except Exception as e:
            logger.warning(f"Credit reconciliation failed for userId={userId}: {e}")

    def getBalanceSnapshot(self, userId: str) -> dict:
        """Login/balance snapshot in tokens, with credits derived. Never raises."""
        defaults = {
            "planTier": "none", "monthlyTokenQuota": 0, "usedTokens": 0,
            "remainingTokens": 0, "monthlyCredits": 0.0, "usedCredits": 0.0,
            "remainingCredits": 0.0, "usagePercentage": 0.0,
            "periodStart": None, "periodEnd": None, "lastResetAt": None,
            "initialized": False,
        }
        try:
            row = self._dbRow(userId)
            if not row:
                return defaults

            tquota = row.get("monthly_token_quota", 0)
            remaining = self.getRemainingTokens(userId)
            if remaining == -1:
                remaining = row.get("remaining_tokens", 0)
            used = max(0, tquota - remaining)
            usagePercent = round((used / tquota) * 100, 2) if tquota > 0 else 0.0

            return {
                "planTier": row.get("plan_tier", "none"),
                "monthlyTokenQuota": tquota,
                "usedTokens": used,
                "remainingTokens": remaining,
                "monthlyCredits": creditMath.tokensToCredits(tquota, TOKEN_TO_CREDIT_RATIO),
                "usedCredits": creditMath.tokensToCredits(used, TOKEN_TO_CREDIT_RATIO),
                "remainingCredits": creditMath.tokensToCredits(remaining, TOKEN_TO_CREDIT_RATIO),
                "usagePercentage": usagePercent,
                "periodStart": row.get("period_start"),
                "periodEnd": row.get("period_end"),
                "lastResetAt": row.get("last_reset_at"),
                "initialized": True,
            }
        except Exception as e:
            logger.warning(f"getBalanceSnapshot failed for userId={userId}: {e}")
            return defaults

    def forceResetAllQuotas(self, resetUsage: bool = False) -> dict:
        """
        Admin-triggered bulk reset.

        Always: recompute monthly_token_quota from config for every
        credit_balances row, then flush all Redis credit hashes.

        When resetUsage=True: also zero used_tokens and restore remaining_tokens
        to the full quota — equivalent to giving every user a fresh monthly
        bucket mid-cycle. The billing period itself is left untouched.

        Returns:
            dict with updatedCount, redisDeletedCount, and mode.
        """
        try:
            rows = (
                self.supabase.table("credit_balances")
                .select("user_id, plan_tier")
                .execute()
            )
            if not rows.data:
                return {"updatedCount": 0, "redisDeletedCount": 0, "mode": "none"}

            now = datetime.now(timezone.utc)
            updatedCount = 0
            for row in rows.data:
                userId = row["user_id"]
                planTier = row.get("plan_tier", "none")
                newQuota = getTokenQuotaForPlan(planTier)

                payload = {
                    "monthly_token_quota": newQuota,
                    "updated_at": now.isoformat(),
                }

                if resetUsage:
                    payload.update({
                        "used_tokens": 0,
                        "remaining_tokens": newQuota,
                        "last_reset_at": now.isoformat(),
                    })

                try:
                    self.supabase.table("credit_balances").update(
                        payload
                    ).eq("user_id", userId).execute()
                    updatedCount += 1
                except Exception as e:
                    logger.warning(f"Force quota update failed for userId={userId}: {e}")

            redisDeletedCount = 0
            try:
                r = self._redis()
                keys = list(r.scan_iter("credits:v3:*"))
                if keys:
                    redisDeletedCount = r.delete(*keys)
            except Exception as e:
                logger.warning(f"Redis bulk delete failed during force reset: {e}")

            mode = "quotaSync+usageReset" if resetUsage else "quotaSync"
            logger.info(
                f"Force quota reset complete — mode={mode}, "
                f"dbUpdated={updatedCount}, redisDeleted={redisDeletedCount}"
            )
            return {
                "updatedCount": updatedCount,
                "redisDeletedCount": int(redisDeletedCount),
                "mode": mode,
            }
        except Exception as e:
            logger.error(f"forceResetAllQuotas failed: {e}")
            raise

    def syncQuotaFromConfig(self, userId: str) -> None:
        """
        Ensure a single user's monthly_token_quota matches the current config.
        Called during reconcile to prevent long-term drift.
        """
        try:
            row = self._dbRow(userId)
            if not row:
                return
            planTier = row.get("plan_tier", "none")
            expectedQuota = getTokenQuotaForPlan(planTier)
            currentQuota = row.get("monthly_token_quota", 0)
            if currentQuota != expectedQuota:
                self.supabase.table("credit_balances").update({
                    "monthly_token_quota": expectedQuota,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("user_id", userId).execute()
                try:
                    r = self._redis()
                    r.hset(self._redisKey(userId), "tquota", expectedQuota)
                except Exception:
                    pass
                logger.info(
                    f"Quota drift corrected for userId={userId}: "
                    f"{currentQuota} -> {expectedQuota}"
                )
        except Exception as e:
            logger.warning(f"syncQuotaFromConfig failed for userId={userId}: {e}")


creditService = CreditService()
