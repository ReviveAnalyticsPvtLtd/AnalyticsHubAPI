"""
Shared helpers for subscription-owned billing and entitlement fields.

The MVP billing cutover keeps identity/profile data on ``Users`` and moves
plan lifecycle, entitlement, pending changes, and Razorpay provider state to
``subscriptions``. These helpers keep the storage shape consistent across API
services, webhooks, and schedulers.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "SUBSCRIPTION_BILLING_FIELDS_SELECT",
    "CANONICAL_SUBSCRIPTION_SELECT",
    "normalizeDomainList",
    "subscriptionExperts",
    "subscriptionPendingRemovals",
    "subscriptionPendingAdditions",
    "subscriptionDomainCount",
    "subscriptionBillingState",
    "subscriptionCustomerId",
    "subscriptionTokenId",
    "subscriptionAnchorDay",
    "subscriptionRecurringFailures",
    "toSubscriptionBillingPayload",
    "toApiPlanFields",
]


SUBSCRIPTION_BILLING_FIELDS_SELECT = (
    "subscribed_experts, domain_count, pending_removals, pending_additions, "
    "billing_state, razorpay_customer_id, razorpay_token_id, "
    "subscription_anchor_day, recurring_failures, cancellation_reason"
)

CANONICAL_SUBSCRIPTION_SELECT = (
    "id, user_id, billing_mode, status, current_period_start, current_period_end, "
    "renewal_due_at, auto_renew_enabled, payment_collection_mode, "
    "default_currency, version, "
    f"{SUBSCRIPTION_BILLING_FIELDS_SELECT}"
)


def normalizeDomainList(value) -> list[str]:
    """
    Normalize DB/API domain list shapes into a clean list of strings.

    Historical data may be comma-separated text; new storage is JSONB array.
    This function is used during migration/cutover logic, not as a fallback to
    the old Users columns.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def subscriptionExperts(subscription: dict | None) -> list[str]:
    return normalizeDomainList((subscription or {}).get("subscribed_experts"))


def subscriptionPendingRemovals(subscription: dict | None) -> list[str]:
    return normalizeDomainList((subscription or {}).get("pending_removals"))


def subscriptionPendingAdditions(subscription: dict | None) -> list[dict]:
    value = (subscription or {}).get("pending_additions")
    return value if isinstance(value, list) else []


def subscriptionDomainCount(subscription: dict | None) -> int:
    subscription = subscription or {}
    count = subscription.get("domain_count")
    if count is None:
        return len(subscriptionExperts(subscription))
    try:
        return int(count)
    except (TypeError, ValueError):
        return len(subscriptionExperts(subscription))


def subscriptionBillingState(subscription: dict | None):
    return (subscription or {}).get("billing_state") or {}


def subscriptionCustomerId(subscription: dict | None) -> str | None:
    return (subscription or {}).get("razorpay_customer_id")


def subscriptionTokenId(subscription: dict | None) -> str | None:
    return (subscription or {}).get("razorpay_token_id")


def subscriptionAnchorDay(subscription: dict | None) -> int | None:
    value = (subscription or {}).get("subscription_anchor_day")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def subscriptionRecurringFailures(subscription: dict | None) -> int:
    value = (subscription or {}).get("recurring_failures")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def toSubscriptionBillingPayload(
    *,
    subscribedExperts=None,
    domainCount=None,
    pendingRemovals=None,
    pendingAdditions=None,
    billingState=None,
    razorpayCustomerId=None,
    razorpayTokenId=None,
    subscriptionAnchorDay=None,
    recurringFailures=None,
    cancellationReason=None,
) -> dict:
    """
    Convert camelCase service arguments into subscription table column names.
    Values left as ``None`` are omitted so callers can do partial updates.
    """
    payload = {}
    if subscribedExperts is not None:
        payload["subscribed_experts"] = normalizeDomainList(subscribedExperts)
    if domainCount is not None:
        payload["domain_count"] = int(domainCount)
    if pendingRemovals is not None:
        payload["pending_removals"] = normalizeDomainList(pendingRemovals)
    if pendingAdditions is not None:
        payload["pending_additions"] = pendingAdditions if isinstance(pendingAdditions, list) else []
    if billingState is not None:
        payload["billing_state"] = billingState or {}
    if razorpayCustomerId is not None:
        payload["razorpay_customer_id"] = razorpayCustomerId
    if razorpayTokenId is not None:
        payload["razorpay_token_id"] = razorpayTokenId
    if subscriptionAnchorDay is not None:
        payload["subscription_anchor_day"] = int(subscriptionAnchorDay)
    if recurringFailures is not None:
        payload["recurring_failures"] = int(recurringFailures)
    if cancellationReason is not None:
        payload["cancellation_reason"] = cancellationReason
    return payload


def toApiPlanFields(subscription: dict | None) -> dict:
    """
    Preserve frontend response shape while sourcing values from subscriptions.
    """
    return {
        "subscribedExperts": subscriptionExperts(subscription),
        "domainCount": subscriptionDomainCount(subscription),
        "pendingRemovals": subscriptionPendingRemovals(subscription),
    }
