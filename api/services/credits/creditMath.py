"""
creditMath.py

Pure, dependency-light math for the single-bucket monthly token system.

No Redis, no DB, no logging — every function is deterministic given its
inputs so the billing-period and conversion logic can be unit-tested in
isolation. The Redis Lua roll and the Python rebuild path both derive their
period boundaries from these helpers.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "rollMonthly",
    "nextPeriodEnd",
    "tokensToCredits",
]


from datetime import datetime
from dateutil.relativedelta import relativedelta


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


def nextPeriodEnd(periodEnd: datetime) -> datetime:
    """The billing period end one calendar month after `periodEnd`."""
    return periodEnd + relativedelta(months=1)


def tokensToCredits(tokens: int, ratio: int) -> float:
    """
    Display conversion: raw tokens -> credits, rounded to 2 decimal places.

    Returns 0.0 for a non-positive token count or a non-positive ratio, so a
    malformed config can never produce a negative or infinite balance.
    """
    if ratio <= 0 or tokens <= 0:
        return 0.0
    return round(tokens / ratio, 2)
