"""
creditMath.py

Pure, dependency-light math for the two-bucket credit system.

No Redis, no DB, no logging — every function is deterministic given its
inputs so the reset/effective/roll logic can be unit-tested in isolation.
Both the Redis Lua hot path and the DB fallback path mirror this logic.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "computeDailyCap",
    "dayStamp",
    "applyDailyReset",
    "effectiveRemaining",
    "rollMonthly",
    "nextUtcMidnight",
    "secondsUntilNextUtcMidnight",
]


from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
import math


def computeDailyCap(monthlyQuota: int, percent: float, exempt: bool) -> int:
    """Daily cap for a plan. Exempt plans get the full monthly quota (no throttle)."""
    if monthlyQuota <= 0:
        return 0
    if exempt:
        return monthlyQuota
    return max(1, math.ceil(monthlyQuota * percent))


def dayStamp(now: datetime) -> int:
    """UTC calendar day encoded as YYYYMMDD (e.g. 20260713)."""
    u = now.astimezone(timezone.utc)
    return u.year * 10000 + u.month * 100 + u.day


def applyDailyReset(dailyUsed: int, storedStamp: int, todayStamp: int) -> tuple[int, int]:
    """Return (dailyUsed, stamp) after a hard daily reset when the day changed."""
    if storedStamp != todayStamp:
        return 0, todayStamp
    return dailyUsed, storedStamp


def effectiveRemaining(monthlyRemaining: int, dailyCap: int, dailyUsed: int) -> int:
    """Spendable-right-now = min(monthly remaining, daily remaining), clamped >= 0."""
    dailyRemaining = max(0, dailyCap - dailyUsed)
    return max(0, min(monthlyRemaining, dailyRemaining))


def rollMonthly(periodEnd: datetime, now: datetime) -> tuple[datetime, datetime]:
    """
    Roll the billing window forward to the current period.

    Advances by whole months until period_end is in the future, covering the
    case where a user was inactive across several missed periods.
    Returns (newPeriodStart, newPeriodEnd).
    """
    ps, pe = periodEnd, periodEnd + relativedelta(months=1)
    while pe <= now:
        ps, pe = pe, pe + relativedelta(months=1)
    return ps, pe


def nextUtcMidnight(now: datetime) -> datetime:
    """The next 00:00:00 UTC strictly after `now`."""
    u = now.astimezone(timezone.utc)
    return (u + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def secondsUntilNextUtcMidnight(now: datetime) -> int:
    """Whole seconds from `now` until the next 00:00 UTC (used for Redis TTL / display)."""
    u = now.astimezone(timezone.utc)
    return int((nextUtcMidnight(u) - u).total_seconds())
