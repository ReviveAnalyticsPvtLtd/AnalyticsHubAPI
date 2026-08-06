"""
billingEngine.py

Centralized backend price and tax computation for all billing flows.

Base prices are per domain and come from config/plans.json via planConfig.
Final invoice-ready amounts combine that base with domain count, billing mode,
tax engine output, and proration windows.

Credit top-up packs follow the same shape but read config/credits.json, since
a pack is priced as one unit rather than per domain.

All outputs are immutable value objects (PricingSnapshot) suitable for
direct persistence to invoice columns. Clients cannot override any
computed amount, tax, or currency field.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["computeInvoiceSnapshot", "computeTopupSnapshot"]


from api.services.billing.planConfig import (
    PLAN_CURRENCY,
    getConfigVersion,
    getPlanForBillingMode,
)
from api.services.billing.taxEngine import computeTax, TaxBreakdown
from utils.logger import logger
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta


@dataclass(frozen=True)
class PricingSnapshot:
    """
    Immutable invoice-ready pricing computation result.

    Once created, this snapshot is frozen and suitable for direct
    persistence to invoice columns. No field may be modified after
    computation.

    Attributes:
        billing_mode: 'monthly_recurring' or 'annual_prepaid'.
        billing_reason: 'initial_purchase', 'renewal', or 'proration'.
        domain_count: Number of active/chargeable domains.
        base_price_per_domain: Per-domain price in paise from the plan.
        amount_before_tax: Total taxable base (domains * price * factor).
        tax: The full TaxBreakdown object.
        total_amount: Final payable amount in paise.
        currency: ISO currency code.
        pricing_version: Snapshot identifier for audit trail.
        plan_id: Internal plan identifier used as price source.
        pricing_reference_snapshot_json: Price-source metadata snapshot.
        period_start: Billing period start (ISO string).
        period_end: Billing period end (ISO string).
    """
    billing_mode: str
    billing_reason: str
    domain_count: int
    base_price_per_domain: int
    amount_before_tax: int
    tax: TaxBreakdown
    total_amount: int
    currency: str
    pricing_version: str
    plan_id: str
    pricing_reference_snapshot_json: dict
    period_start: str
    period_end: str

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialization and invoice persistence."""
        result = asdict(self)
        result["tax_amount"] = self.tax.tax_amount
        result["tax_rule_version"] = self.tax.tax_rule_version
        result["place_of_supply_snapshot"] = self.tax.place_of_supply_snapshot
        result["tax_breakdown_json"] = self.tax.to_dict()
        return result


def computeInvoiceSnapshot(billingMode: str, billingReason: str,
                           domainCount: int, customerState: str = None,
                           periodStart: datetime = None,
                           periodEnd: datetime = None,
                           prorationAnchorStart: datetime = None,
                           prorationAnchorEnd: datetime = None) -> PricingSnapshot:
    """
    Compute an immutable invoice-ready pricing snapshot.

    Supports all billing scenarios:
        - monthly_recurring initial_purchase: domainCount * monthlyBase + tax
        - monthly_recurring renewal: domainCount * monthlyBase + tax
        - annual_prepaid initial_purchase: domainCount * annualBase + tax
        - annual_prepaid renewal: domainCount * annualBase + tax
        - proration (add-domain mid-cycle): domainCount * dailyRate * remainingDays + tax

    Args:
        billingMode: 'monthly_recurring' or 'annual_prepaid'.
        billingReason: 'initial_purchase', 'renewal', or 'proration'.
        domainCount: Number of domains to charge for.
        customerState: Customer billing state code for tax determination.
        periodStart: Billing period start (required for renewal/initial).
        periodEnd: Billing period end (required for renewal/initial).
        prorationAnchorStart: Start of remaining period (proration only).
        prorationAnchorEnd: End of remaining period (proration only).

    Returns:
        PricingSnapshot: Frozen, immutable pricing result.

    Raises:
        ValueError: If billing mode/reason combination is unsupported or
            required period arguments are missing.
    """
    now = datetime.now(timezone.utc)

    if billingMode not in ("monthly_recurring", "annual_prepaid"):
        raise ValueError(f"Unsupported billing mode: {billingMode}")

    plan = getPlanForBillingMode(billingMode)
    basePrice = plan["amount_per_domain"]
    planId = plan["plan_id"]
    priceRef = {
        "source": "config_plans_json",
        "configVersion": getConfigVersion(),
        "planKey": plan["planKey"],
        "planId": planId,
        "amount": basePrice,
        "currency": PLAN_CURRENCY,
        "fetched_at": now.isoformat(),
    }

    if periodStart is None:
        periodStart = now
    if periodEnd is None:
        if billingMode == "monthly_recurring":
            periodEnd = periodStart + relativedelta(months=1)
        else:
            periodEnd = periodStart + relativedelta(years=1)

    if billingReason in ("initial_purchase", "renewal"):
        amountBeforeTax = domainCount * basePrice
    elif billingReason == "proration":
        if not prorationAnchorStart or not prorationAnchorEnd:
            raise ValueError(
                "prorationAnchorStart and prorationAnchorEnd are required "
                "for proration billing reason"
            )
        totalDays = (prorationAnchorEnd - prorationAnchorStart).days
        if totalDays <= 0:
            totalDays = 1
        remainingDays = (prorationAnchorEnd - now).days
        if remainingDays <= 0:
            remainingDays = 1
        dailyRate = basePrice / totalDays
        amountBeforeTax = int(round(domainCount * dailyRate * remainingDays))
        periodStart = now
        periodEnd = prorationAnchorEnd
    else:
        raise ValueError(f"Unsupported billing reason: {billingReason}")

    taxBreakdown = computeTax(
        amountBeforeTax=amountBeforeTax,
        customerState=customerState,
        invoiceTimestamp=now,
    )

    pricingVersion = f"v1_{planId}_{int(now.timestamp())}"

    snapshot = PricingSnapshot(
        billing_mode=billingMode,
        billing_reason=billingReason,
        domain_count=domainCount,
        base_price_per_domain=basePrice,
        amount_before_tax=amountBeforeTax,
        tax=taxBreakdown,
        total_amount=taxBreakdown.total_amount,
        currency="INR",
        pricing_version=pricingVersion,
        plan_id=planId,
        pricing_reference_snapshot_json=priceRef,
        period_start=periodStart.isoformat() if isinstance(periodStart, datetime) else str(periodStart),
        period_end=periodEnd.isoformat() if isinstance(periodEnd, datetime) else str(periodEnd),
    )

    logger.info(
        f"Invoice snapshot computed — mode={billingMode}, reason={billingReason}, "
        f"domains={domainCount}, base={amountBeforeTax}, tax={taxBreakdown.tax_amount}, "
        f"total={snapshot.total_amount}, version={pricingVersion}"
    )

    return snapshot


def computeTopupSnapshot(packId: str, billingMode: str,
                         customerState: str = None) -> PricingSnapshot:
    """
    Compute an invoice-ready pricing snapshot for a credit top-up pack.

    Unlike the subscription snapshots, the price comes from config/credits.json
    rather than a Razorpay reference plan — top-up packs are not Razorpay plans.
    PricingSnapshot is domain-shaped, so domain_count is 1 and
    base_price_per_domain is the pack price, read as "one pack".

    The pack's token count is frozen into pricing_reference_snapshot_json here,
    and grant_topup_tokens reads it back off the invoice at payment time. A
    later config change therefore cannot alter what an in-flight order grants.

    Args:
        packId: Pack key from config, e.g. 'medium'.
        billingMode: The subscriber's billing mode, recorded for reporting.
        customerState: Customer billing state code for tax determination.

    Returns:
        PricingSnapshot: Frozen, immutable pricing result.

    Raises:
        ValueError: If the pack is unknown or retired.
    """
    from api.services.credits.creditConfig import getTopupPack, CREDIT_CONFIG

    pack = getTopupPack(packId)
    if pack is None:
        raise ValueError(f"Unknown or inactive top-up pack: {packId}")

    now = datetime.now(timezone.utc)
    amountBeforeTax = pack["amount"]
    taxBreakdown = computeTax(
        amountBeforeTax=amountBeforeTax,
        customerState=customerState,
        invoiceTimestamp=now,
    )

    configVersion = CREDIT_CONFIG.get("version", "unknown")
    priceRef = {
        "source": "config_credits_json",
        "configVersion": configVersion,
        "packId": packId,
        "tokens": pack["tokens"],
        "amount": amountBeforeTax,
        "fetched_at": now.isoformat(),
    }
    pricingVersion = f"topup_v{configVersion}_{packId}_{int(now.timestamp())}"

    snapshot = PricingSnapshot(
        billing_mode=billingMode,
        billing_reason="add_on",
        domain_count=1,
        base_price_per_domain=amountBeforeTax,
        amount_before_tax=amountBeforeTax,
        tax=taxBreakdown,
        total_amount=taxBreakdown.total_amount,
        currency="INR",
        pricing_version=pricingVersion,
        plan_id=f"topup_{packId}",
        pricing_reference_snapshot_json=priceRef,
        period_start=now.isoformat(),
        period_end=now.isoformat(),
    )

    logger.info(
        f"Top-up snapshot computed — pack={packId}, tokens={pack['tokens']}, "
        f"base={amountBeforeTax}, tax={taxBreakdown.tax_amount}, "
        f"total={snapshot.total_amount}, version={pricingVersion}"
    )

    return snapshot
