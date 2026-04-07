"""
Canonical subscription status resolution for API responses.

Aligns displayed status with `Users` row data and `SubscriptionStatus` in api.models:
NONE (no history), TRIAL (free trial window), ACTIVE (paying),
CANCELLED (terminated via provider), PENDING_CANCELLATION, PAUSED, EXPIRED (ended).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["parse_expiry_utc", "resolve_subscription_status", "coerce_subscription_expiry"]


def coerce_subscription_expiry(expiry: Any) -> str | None:
    """Normalize DB or pandas expiry values to a string suitable for parse_expiry_utc."""
    return _coerce_expiry_any(expiry)


def _pandas_is_na(val: Any) -> bool:
    try:
        import pandas as pd
        return bool(pd.isna(val))
    except ImportError:
        return False


def parse_expiry_utc(expiry_str: str | None) -> datetime | None:
    """
    Parse subscription expiry to UTC-aware datetime. Returns None if missing or invalid.
    """
    if expiry_str is None:
        return None
    s = str(expiry_str).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_expiry_any(expiry: Any) -> str | None:
    if expiry is None or _pandas_is_na(expiry):
        return None
    if isinstance(expiry, float) and math.isnan(expiry):
        return None
    s = str(expiry).strip()
    if not s or s.lower() in ("none", "null", "nat"):
        return None
    return s


def _normalize_stored(stored: Any) -> str | None:
    if stored is None or _pandas_is_na(stored):
        return None
    if isinstance(stored, float) and math.isnan(stored):
        return None
    s = str(stored).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    return s


def resolve_subscription_status(
    stored: Any,
    expiry_str: str | None,
    now_utc: datetime,
) -> str:
    """
    Return the subscription status string clients should see.

    - No valid expiry: use DB value, or NONE if empty.
    - Future expiry: use DB value; if missing, ACTIVE (legacy rows).
    - Past expiry: preserve CANCELLED, EXPIRED, PAUSED; map stale access states to EXPIRED.
    """
    expiry = parse_expiry_utc(_coerce_expiry_any(expiry_str))
    normalized = _normalize_stored(stored)

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    if expiry is None:
        return normalized if normalized else "NONE"

    if expiry > now_utc:
        if not normalized:
            return "ACTIVE"
        return normalized

    if normalized in ("CANCELLED", "EXPIRED"):
        return normalized
    if normalized == "PAUSED":
        return "PAUSED"
    if normalized in ("TRIAL", "ACTIVE", "PENDING_CANCELLATION", "NONE") or not normalized:
        return "EXPIRED"
    return "EXPIRED"
