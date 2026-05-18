"""
taxEngine.py

Server-side tax computation engine backed by config-file rules.

Resolves the applicable GST rule for a given product, jurisdiction, and
timestamp, then computes intra-state (CGST+SGST) or inter-state (IGST)
tax breakdown against a taxable base amount.

All tax computations are authoritative and server-only. Clients cannot
pass tax rates or amounts. The output includes a frozen snapshot of the
rule version and place-of-supply determination for invoice persistence.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["computeTax"]


from api.services.billing.taxConfigLoader import getTaxConfig
from utils.logger import logger
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from dateutil import parser as dtparser


SELLER_STATE = "KA"


@dataclass(frozen=True)
class TaxBreakdown:
    """
    Immutable tax computation result.

    Attributes:
        amount_before_tax: Taxable base in paise.
        cgst: CGST amount in paise (intra-state only).
        sgst: SGST amount in paise (intra-state only).
        igst: IGST amount in paise (inter-state only).
        cess: Cess amount in paise.
        tax_amount: Total tax in paise (cgst+sgst+igst+cess).
        total_amount: amount_before_tax + tax_amount.
        tax_rule_version: Version string from the config.
        place_of_supply_snapshot: Resolved supply state used for
            intra/inter determination.
        is_intra_state: True if seller and supply states match.
    """
    amount_before_tax: int
    cgst: int
    sgst: int
    igst: int
    cess: int
    tax_amount: int
    total_amount: int
    tax_rule_version: str
    place_of_supply_snapshot: str
    is_intra_state: bool

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialization."""
        return asdict(self)


def _resolveRule(productTaxCode: str, jurisdictionScope: str,
                 invoiceTimestamp: datetime) -> dict | None:
    """
    Find the applicable tax rule from the config by product code,
    jurisdiction, and effective-date window.

    Lookup order:
        1. Exact jurisdiction match (e.g. 'IN-KA').
        2. Broader fallback (e.g. 'IN').
        3. None if no rule matches.

    Args:
        productTaxCode: Logical product code (e.g. 'saas_subscription').
        jurisdictionScope: Target jurisdiction (e.g. 'IN-KA', 'IN').
        invoiceTimestamp: Timezone-aware UTC timestamp.

    Returns:
        dict | None: The matching rule entry or None.
    """
    config = getTaxConfig()
    rules = config.get("rules", [])

    if invoiceTimestamp.tzinfo is None:
        invoiceTimestamp = invoiceTimestamp.replace(tzinfo=timezone.utc)

    candidates = []
    for rule in rules:
        if rule["product_tax_code"] != productTaxCode:
            continue

        effectiveFrom = dtparser.isoparse(rule["effective_from"])
        if effectiveFrom.tzinfo is None:
            effectiveFrom = effectiveFrom.replace(tzinfo=timezone.utc)

        effectiveTo = rule.get("effective_to")
        if effectiveTo is not None:
            effectiveTo = dtparser.isoparse(effectiveTo)
            if effectiveTo.tzinfo is None:
                effectiveTo = effectiveTo.replace(tzinfo=timezone.utc)

        if invoiceTimestamp < effectiveFrom:
            continue
        if effectiveTo is not None and invoiceTimestamp >= effectiveTo:
            continue

        candidates.append(rule)

    exactMatch = None
    fallbackMatch = None
    for candidate in candidates:
        scope = candidate["jurisdiction_scope"]
        if scope == jurisdictionScope:
            exactMatch = candidate
            break
        if jurisdictionScope.startswith(scope) or scope == "IN":
            fallbackMatch = candidate

    return exactMatch or fallbackMatch


def _roundPaise(amount: Decimal, mode: str, scale: int) -> int:
    """
    Round a decimal paise amount to an integer using the specified mode.

    Args:
        amount: Decimal amount in paise.
        mode: Rounding mode string (e.g. 'HALF_UP').
        scale: Decimal scale (unused for paise but kept for future).

    Returns:
        int: Rounded paise amount.
    """
    roundingModes = {
        "HALF_UP": ROUND_HALF_UP,
    }
    roundingMode = roundingModes.get(mode, ROUND_HALF_UP)
    return int(amount.quantize(Decimal("1"), rounding=roundingMode))


def computeTax(amountBeforeTax: int, customerState: str = None,
               productTaxCode: str = "saas_subscription",
               jurisdictionScope: str = "IN",
               invoiceTimestamp: datetime = None) -> TaxBreakdown:
    """
    Compute tax breakdown for a given taxable base amount.

    Determines intra-state vs inter-state based on seller state (KA)
    and customer state. Falls back to seller state if customer state
    is unavailable.

    Args:
        amountBeforeTax: Taxable base amount in paise.
        customerState: Customer billing state code (e.g. 'KA', 'MH').
        productTaxCode: Product classification for rule lookup.
        jurisdictionScope: Jurisdiction scope for rule lookup.
        invoiceTimestamp: UTC timestamp for effective-date resolution.

    Returns:
        TaxBreakdown: Frozen, immutable tax computation result.

    Raises:
        ValueError: If no applicable tax rule is found.
    """
    if invoiceTimestamp is None:
        invoiceTimestamp = datetime.now(timezone.utc)

    config = getTaxConfig()
    taxRuleVersion = config.get("version", "unknown")

    supplyState = customerState or SELLER_STATE
    isIntraState = supplyState.upper() == SELLER_STATE.upper()

    fullJurisdiction = f"IN-{supplyState.upper()}" if customerState else jurisdictionScope
    rule = _resolveRule(productTaxCode, fullJurisdiction, invoiceTimestamp)
    if rule is None:
        raise ValueError(
            f"No applicable tax rule found for product={productTaxCode}, "
            f"jurisdiction={fullJurisdiction}, timestamp={invoiceTimestamp.isoformat()}"
        )

    innerRule = rule["rule"]
    roundingConfig = innerRule.get("rounding", {})
    roundingMode = roundingConfig.get("mode", "HALF_UP")
    roundingScale = roundingConfig.get("scale", 2)

    baseDecimal = Decimal(str(amountBeforeTax))
    cessRate = Decimal(str(innerRule.get("cess", 0)))

    cgstAmount = 0
    sgstAmount = 0
    igstAmount = 0

    if isIntraState:
        cgstRate = Decimal(str(innerRule["intra_state"]["cgst"]))
        sgstRate = Decimal(str(innerRule["intra_state"]["sgst"]))
        cgstAmount = _roundPaise(baseDecimal * cgstRate / Decimal("100"), roundingMode, roundingScale)
        sgstAmount = _roundPaise(baseDecimal * sgstRate / Decimal("100"), roundingMode, roundingScale)
    else:
        igstRate = Decimal(str(innerRule["inter_state"]["igst"]))
        igstAmount = _roundPaise(baseDecimal * igstRate / Decimal("100"), roundingMode, roundingScale)

    cessAmount = _roundPaise(baseDecimal * cessRate / Decimal("100"), roundingMode, roundingScale)
    taxAmount = cgstAmount + sgstAmount + igstAmount + cessAmount
    totalAmount = amountBeforeTax + taxAmount

    breakdown = TaxBreakdown(
        amount_before_tax=amountBeforeTax,
        cgst=cgstAmount,
        sgst=sgstAmount,
        igst=igstAmount,
        cess=cessAmount,
        tax_amount=taxAmount,
        total_amount=totalAmount,
        tax_rule_version=taxRuleVersion,
        place_of_supply_snapshot=supplyState.upper(),
        is_intra_state=isIntraState,
    )

    logger.info(
        f"Tax computed — base={amountBeforeTax}, tax={taxAmount}, "
        f"total={totalAmount}, rule={taxRuleVersion}, "
        f"supply={supplyState.upper()}, intra={isIntraState}"
    )

    return breakdown
