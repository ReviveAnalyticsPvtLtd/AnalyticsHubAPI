"""
planConfig.py

Loads and exposes the subscription plan catalogue from config/plans.json.
Follows the creditConfig.py pattern — loads once at import time, cached
in-memory, with a reload function for hot-reloading.

Plan prices are per domain, in paise, and are server-authoritative. The client
sends a billing mode; it never sends an amount. Prices used to come from
Razorpay reference plans, which meant a network round trip to a payments API
the backend never asked to apply that plan. They are backend data now, ready
for an admin panel to edit.

The `fx_reference` block records the USD figures and the exchange rate the INR
amounts were derived from. It is documentation and is not read at runtime.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "PLAN_CONFIG",
    "PLAN_CURRENCY",
    "getPlanForBillingMode",
    "getPlans",
    "getMaxDomains",
    "getConfigVersion",
    "reloadPlanConfig",
    "buildPlanCatalogue",
]


from utils.logger import logger
import json
import os


_CONFIG_PATH = "config/plans.json"

_DEFAULT_MAX_DOMAINS = 4

_cachedPlanConfig: dict | None = None


def _loadFromFile(filePath: str) -> dict:
    """
    Read and validate the plan config JSON from disk.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If required keys are missing.
    """
    resolvedPath = os.path.abspath(filePath)
    if not os.path.isfile(resolvedPath):
        raise FileNotFoundError(
            f"Plan config file not found at '{resolvedPath}'. "
            f"Ensure config/plans.json exists in the project root."
        )

    with open(resolvedPath, "r", encoding="utf-8") as f:
        config = json.load(f)

    requiredKeys = {"version", "currency", "plans"}
    missing = requiredKeys - set(config.keys())
    if missing:
        raise ValueError(f"Plan config missing required keys: {missing}")

    seenBillingModes: dict[str, str] = {}
    for planKey, plan in config.get("plans", {}).items():
        if not plan.get("active", False):
            continue
        billingMode = plan.get("billing_mode")
        if billingMode in seenBillingModes:
            raise ValueError(
                f"Duplicate billing_mode '{billingMode}' among active plans: "
                f"'{seenBillingModes[billingMode]}' and '{planKey}'. "
                f"getPlanForBillingMode would silently pick whichever comes "
                f"first in dict order — deactivate one before reloading."
            )
        seenBillingModes[billingMode] = planKey

    return config


def reloadPlanConfig() -> dict:
    """Force-reload the plan configuration from disk."""
    global _cachedPlanConfig
    try:
        config = _loadFromFile(_CONFIG_PATH)
        _cachedPlanConfig = config
        logger.info(
            f"Plan config loaded — version={config.get('version')}, "
            f"currency={config.get('currency')}, "
            f"plans={list(config.get('plans', {}).keys())}"
        )
        return config
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Plan config load failed: {e}")
        raise RuntimeError(f"Plan config load failed: {e}") from e


def _getPlanConfig() -> dict:
    """Return the cached plan configuration, loading on first call."""
    global _cachedPlanConfig
    if _cachedPlanConfig is not None:
        return _cachedPlanConfig
    return reloadPlanConfig()


PLAN_CONFIG = _getPlanConfig()
PLAN_CURRENCY = PLAN_CONFIG.get("currency", "INR")


def getPlans() -> dict:
    """
    Return the purchasable plans, keyed by plan key.

    Inactive plans are excluded so a retired plan disappears from the purchase
    menu without invalidating historical invoices that reference its plan_id.

    Returns:
        dict: Active plans, each with 'amount_per_domain' (paise) and 'plan_id'.
    """
    plans = _getPlanConfig().get("plans", {})
    return {key: plan for key, plan in plans.items() if plan.get("active", False)}


def getPlanForBillingMode(billingMode: str) -> dict:
    """
    Return the active plan that serves the given billing mode.

    Args:
        billingMode: 'monthly_recurring' or 'annual_prepaid'.

    Returns:
        dict: The plan, with an added 'planKey' naming it in the catalogue.

    Raises:
        ValueError: If no active plan serves that billing mode.
    """
    for planKey, plan in getPlans().items():
        if plan.get("billing_mode") == billingMode:
            return {**plan, "planKey": planKey}
    raise ValueError(f"No active plan configured for billing mode: {billingMode}")


def getMaxDomains() -> int:
    """Return the maximum number of domains a subscription may hold."""
    limits = _getPlanConfig().get("limits", {})
    return limits.get("max_domains", _DEFAULT_MAX_DOMAINS)


def getConfigVersion() -> str:
    """
    Return the live config version, re-read rather than captured at import.

    PLAN_CONFIG is a module constant frozen at import time, so it would report
    a stale version after reloadPlanConfig(). Anything that stamps a version
    onto a persisted invoice must use this instead.
    """
    return _getPlanConfig().get("version", "unknown")


def buildPlanCatalogue() -> dict:
    """
    Compose the client-facing plan catalogue.

    Price comes from this module; the included credit allowance comes from
    creditConfig, which owns all token math. Neither figure is duplicated
    across the two config files.

    Amounts are per domain, in paise, and pre-tax — tax is computed at invoice
    time from the customer's place of supply.

    Returns:
        dict: {"plans": [...], "currency": str, "maxDomains": int,
               "configVersion": str}.
    """
    from api.services.credits.creditConfig import (
        TOKEN_TO_CREDIT_RATIO,
        getTokenQuotaForPlan,
    )

    plans = []
    for planKey, plan in getPlans().items():
        includedTokens = getTokenQuotaForPlan(planKey, 1)
        if includedTokens <= 0:
            logger.error(
                f"Plan '{planKey}' has no credit quota in config/credits.json — "
                f"omitting it from the catalogue rather than advertising a paid "
                f"plan with zero included credits."
            )
            continue
        plans.append({
            "planKey": planKey,
            "planId": plan.get("plan_id"),
            "billingMode": plan.get("billing_mode"),
            "amountPerDomain": plan.get("amount_per_domain"),
            "currency": PLAN_CURRENCY,
            "includedCreditsPerDomain": includedTokens // TOKEN_TO_CREDIT_RATIO,
            "description": plan.get("description", ""),
        })

    return {
        "plans": plans,
        "currency": PLAN_CURRENCY,
        "maxDomains": getMaxDomains(),
        "configVersion": getConfigVersion(),
    }
