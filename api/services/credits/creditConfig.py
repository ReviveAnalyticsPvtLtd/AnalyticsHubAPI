"""
creditConfig.py

Loads and exposes the credit system configuration from config/credits.json.
Follows the taxConfigLoader.py pattern — loads once at import time, cached
in-memory, with a reload function for hot-reloading.

Quotas and operation minimums are expressed in raw LLM tokens. Credits are a
display unit only: credits = tokens / TOKEN_TO_CREDIT_RATIO.

Top-up packs are the purchasable bundles of extra tokens. Their prices are in
paise and are server-authoritative — the client sends only a pack id.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "CREDIT_CONFIG",
    "TOKEN_TO_CREDIT_RATIO",
    "TOPUP_PACKS",
    "getTokenQuotaForPlan",
    "getOperationMinimum",
    "getTopupPacks",
    "getTopupPack",
    "reloadCreditConfig",
]


from utils.logger import logger
import json
import os


_CREDIT_CONFIG_PATH = "config/credits.json"

_DEFAULT_OPERATION_MINIMUM = 1000

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

    requiredKeys = {"version", "token_to_credit_ratio", "quotas", "operation_minimums"}
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
TOKEN_TO_CREDIT_RATIO = CREDIT_CONFIG.get("token_to_credit_ratio", 10000)
TOPUP_PACKS = CREDIT_CONFIG.get("topup_packs", {})


def getTokenQuotaForPlan(planType: str) -> int:
    """
    Return the monthly token quota for the given plan tier.

    Args:
        planType: One of 'none', 'free', 'pro', 'annual'.

    Returns:
        int: Monthly token quota. Defaults to 0 for unknown plans.
    """
    quotas = _getCreditConfig().get("quotas", {})
    planInfo = quotas.get(planType, quotas.get("none", {}))
    return planInfo.get("monthly_tokens", 0)


def getOperationMinimum(operationType: str) -> int:
    """
    Return the minimum remaining tokens required before allowing an operation.

    Args:
        operationType: Operation key from config, e.g. 'reporting_query'.

    Returns:
        int: Minimum token threshold. Defaults to 1000 for unknown operations.
    """
    minimums = _getCreditConfig().get("operation_minimums", {})
    return minimums.get(operationType, _DEFAULT_OPERATION_MINIMUM)


def getTopupPacks() -> dict:
    """
    Return the purchasable top-up packs, keyed by pack id.

    Inactive packs are excluded so a retired pack disappears from the purchase
    menu without invalidating historical invoices that reference its id.

    Returns:
        dict: Active packs, each with 'tokens' and 'amount' (paise).
    """
    packs = _getCreditConfig().get("topup_packs", {})
    return {packId: pack for packId, pack in packs.items() if pack.get("active", False)}


def getTopupPack(packId: str) -> dict | None:
    """
    Return a single active top-up pack.

    Args:
        packId: Pack key from config, e.g. 'medium'.

    Returns:
        dict | None: The pack, or None if it is unknown or retired.
    """
    return getTopupPacks().get(packId)
