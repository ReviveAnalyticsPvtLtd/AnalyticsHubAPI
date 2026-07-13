"""
creditConfig.py

Loads and exposes the credit system configuration from config/credits.json.
Follows the taxConfigLoader.py pattern — loads once at import time, cached
in-memory, with a reload function for hot-reloading.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "CREDIT_CONFIG",
    "TOKEN_TO_CREDIT_RATIO",
    "DAILY_CREDIT_PERCENT",
    "getQuotaForPlan",
    "getDailyCapForPlan",
    "isPlanDailyExempt",
    "getOperationMinimum",
    "reloadCreditConfig",
]


from utils.logger import logger
import json
import os


_CREDIT_CONFIG_PATH = "config/credits.json"

_cachedCreditConfig: dict | None = None


def _loadFromFile(filePath: str) -> dict:
    """
    Read and validate the credit config JSON from disk.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If required keys are missing.
    """
    resolvedPath = os.path.abspath(filePath)
    if not os.path.isfile(resolvedPath):
        raise FileNotFoundError(
            f"Credit config file not found at '{resolvedPath}'. "
            f"Ensure config/credits.json exists in the project root."
        )

    with open(resolvedPath, "r", encoding="utf-8") as f:
        config = json.load(f)

    requiredKeys = {"version", "token_to_credit_ratio", "daily_credit_percent", "quotas", "operation_minimums"}
    missing = requiredKeys - set(config.keys())
    if missing:
        raise ValueError(f"Credit config missing required keys: {missing}")

    return config


def reloadCreditConfig() -> dict:
    """Force-reload the credit configuration from disk."""
    global _cachedCreditConfig
    try:
        config = _loadFromFile(_CREDIT_CONFIG_PATH)
        _cachedCreditConfig = config
        logger.info(
            f"Credit config loaded — version={config.get('version')}, "
            f"ratio={config.get('token_to_credit_ratio')}, "
            f"plans={list(config.get('quotas', {}).keys())}"
        )
        return config
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Credit config load failed: {e}")
        raise RuntimeError(f"Credit config load failed: {e}") from e


def _getCreditConfig() -> dict:
    """Return the cached credit configuration, loading on first call."""
    global _cachedCreditConfig
    if _cachedCreditConfig is not None:
        return _cachedCreditConfig
    return reloadCreditConfig()


CREDIT_CONFIG = _getCreditConfig()
TOKEN_TO_CREDIT_RATIO = CREDIT_CONFIG.get("token_to_credit_ratio", 100)
DAILY_CREDIT_PERCENT = CREDIT_CONFIG.get("daily_credit_percent", 0.05)


def getQuotaForPlan(planType: str) -> int:
    """
    Return the monthly credit quota for the given plan tier.

    Args:
        planType: One of 'none', 'free', 'pro', 'annual'.

    Returns:
        int: Monthly credit quota. Defaults to 0 for unknown plans.
    """
    quotas = _getCreditConfig().get("quotas", {})
    planInfo = quotas.get(planType, quotas.get("none", {}))
    return planInfo.get("monthly_credits", 0)


def isPlanDailyExempt(planType: str) -> bool:
    """
    Whether a plan is exempt from the daily throttle.

    Exempt plans (free/trial, none) can spend their full monthly quota with no
    daily cap. Defaults to False for unknown plans.
    """
    quotas = _getCreditConfig().get("quotas", {})
    planInfo = quotas.get(planType, quotas.get("none", {}))
    return bool(planInfo.get("daily_exempt", False))


def getDailyCapForPlan(planType: str) -> int:
    """
    Return the daily credit cap for a plan tier.

    Exempt plans (free/none) return the full monthly quota (no throttle);
    others return ceil(monthly_credits * daily_credit_percent), min 1 for a
    non-zero quota.
    """
    from api.services.credits.creditMath import computeDailyCap

    monthly = getQuotaForPlan(planType)
    return computeDailyCap(monthly, DAILY_CREDIT_PERCENT, isPlanDailyExempt(planType))


def getOperationMinimum(operationType: str) -> int:
    """
    Return the minimum credits required before allowing an operation.

    Args:
        operationType: Operation key from config, e.g. 'reporting_query'.

    Returns:
        int: Minimum credit threshold. Defaults to 1 for unknown operations.
    """
    minimums = _getCreditConfig().get("operation_minimums", {})
    return minimums.get(operationType, 1)
