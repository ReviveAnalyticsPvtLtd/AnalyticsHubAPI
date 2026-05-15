"""
billingConfig.py

Centralized billing configuration for the annual-plan implementation.
Threshold values and billing-related environment defaults are read once
at import time and exposed as module-level constants for use across
services and schedulers.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "MAX_SILENT_RECURRING_AMOUNT_PAISE",
    "RAZORPAY_ANNUAL_PLAN_ID",
]


from utils.logger import logger
import os


MAX_SILENT_RECURRING_AMOUNT_PAISE: int = int(
    os.environ.get("MAX_SILENT_RECURRING_AMOUNT_PAISE", "1500000")
)

RAZORPAY_ANNUAL_PLAN_ID: str = os.environ.get(
    "RAZORPAY_ANNUAL_PLAN_ID", ""
)


logger.info(
    "billingConfig loaded — "
    f"maxSilentRecurring={MAX_SILENT_RECURRING_AMOUNT_PAISE}"
)
