"""
billingClient.py — shared clients for the billing package.

Both the FastAPI app and the Celery workers transitively import api.commons,
which already owns a singleton Supabase client. Re-export it here so billing
services don't each re-instantiate their own. Also owns the shared Redis
factory used by the billing schedulers (decode_responses=True for the
lock/advisory-lock path).
"""

from api.commons import client as supabaseClient
import redis
import os


def getBillingRedisClient() -> redis.Redis:
    """Create a Redis client for billing scheduler advisory locks."""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        password=os.environ.get("REDIS_PASSWORD", None),
        decode_responses=True,
    )