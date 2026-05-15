"""
billingModels.py

Pydantic models and enums for the annual-plan billing system.

These models define:
    - Domain enums for billing mode, subscription status, invoice status,
      payment attempt status, and payment collection modes.
    - Read/write schemas for ``subscriptions``, extended ``Invoices``,
      and ``payment_attempts`` tables introduced in Phase 1.

All enum values align with the CHECK constraints defined in the SQL
migration scripts (``docs/annualPlan/sql/``).
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "BillingMode",
    "SubscriptionLifecycleStatus",
    "PaymentCollectionMode",
    "InvoiceBillingReason",
    "InvoicePaymentFlow",
    "InvoiceLifecycleStatus",
    "PaymentAttemptType",
    "PaymentAttemptStatus",
    "SubscriptionRecord",
    "InvoiceRecord",
    "PaymentAttemptRecord",
]


from pydantic import BaseModel
from datetime import datetime
from enum import Enum


class BillingMode(str, Enum):
    """Matches ``subscriptions.billing_mode`` CHECK constraint."""
    MONTHLY_RECURRING = "monthly_recurring"
    ANNUAL_PREPAID = "annual_prepaid"


class SubscriptionLifecycleStatus(str, Enum):
    """Matches ``subscriptions.status`` CHECK constraint."""
    ACTIVE = "active"
    RENEWAL_UPCOMING = "renewal_upcoming"
    PAYMENT_PENDING = "payment_pending"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentCollectionMode(str, Enum):
    """Matches ``subscriptions.payment_collection_mode`` CHECK constraint."""
    SILENT_TOKEN = "silent_token"
    AUTHENTICATED_CHECKOUT = "authenticated_checkout"
    INVOICE_LINK = "invoice_link"


class InvoiceBillingReason(str, Enum):
    """Matches ``Invoices.billing_reason`` CHECK constraint."""
    INITIAL_PURCHASE = "initial_purchase"
    RENEWAL = "renewal"
    PRORATION = "proration"
    ADD_ON = "add_on"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class InvoicePaymentFlow(str, Enum):
    """Matches ``Invoices.payment_flow`` CHECK constraint."""
    TOKEN_CHARGE = "token_charge"
    RAZORPAY_ORDER_CHECKOUT = "razorpay_order_checkout"
    RAZORPAY_INVOICE = "razorpay_invoice"
    RAZORPAY_PAYMENT_LINK = "razorpay_payment_link"


class InvoiceLifecycleStatus(str, Enum):
    """Normalized invoice lifecycle status values."""
    DRAFT = "draft"
    UPCOMING = "upcoming"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    VOID = "void"


class PaymentAttemptType(str, Enum):
    """Matches ``payment_attempts.attempt_type`` CHECK constraint."""
    TOKEN_DEBIT = "token_debit"
    CHECKOUT = "checkout"
    INVOICE_PAY = "invoice_pay"
    PAYMENT_LINK_PAY = "payment_link_pay"
    RECONCILIATION_UPDATE = "reconciliation_update"


class PaymentAttemptStatus(str, Enum):
    """Matches ``payment_attempts.status`` CHECK constraint."""
    CREATED = "created"
    PRECHECK_FAILED = "precheck_failed"
    PENDING_PROVIDER_ACK = "pending_provider_ack"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SubscriptionRecord(BaseModel):
    """
    Read/write schema for a row in the ``subscriptions`` table.

    All optional fields are nullable in the DB and may be absent
    when reading partial selects.
    """
    id: str | None = None
    user_id: str
    billing_mode: BillingMode
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    renewal_due_at: datetime | None = None
    auto_renew_enabled: bool = True
    payment_collection_mode: PaymentCollectionMode = PaymentCollectionMode.SILENT_TOKEN
    status: SubscriptionLifecycleStatus = SubscriptionLifecycleStatus.ACTIVE
    default_currency: str = "INR"
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InvoiceRecord(BaseModel):
    """
    Schema for the extended columns added to ``Invoices`` by
    ``002_extend_invoices.sql``.

    Existing legacy columns (``userId``, ``razorpayInvoiceId``, etc.)
    are not duplicated here; this covers only the new fields.
    """
    id: str | None = None
    subscription_id: str | None = None
    billing_reason: InvoiceBillingReason | None = None
    payment_flow: InvoicePaymentFlow | None = None
    requires_customer_auth: bool = False
    razorpay_order_id: str | None = None
    razorpay_payment_link_id: str | None = None
    provider_receipt: str | None = None
    due_date: datetime | None = None
    expires_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    amount_before_tax: int | None = None
    tax_amount: int | None = None
    total_amount: int | None = None
    tax_breakdown_json: dict | None = None
    tax_rule_version: str | None = None
    place_of_supply_snapshot: str | None = None
    pricing_version: str | None = None
    pricing_reference_snapshot_json: dict | None = None
    metadata_json: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PaymentAttemptRecord(BaseModel):
    """
    Read/write schema for a row in the ``payment_attempts`` table.
    """
    id: str | None = None
    invoice_id: str | None = None
    user_id: str
    subscription_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    cycle_key: str | None = None
    provider: str = "razorpay"
    provider_payment_id: str | None = None
    provider_order_id: str | None = None
    provider_invoice_id: str | None = None
    provider_payment_link_id: str | None = None
    attempt_type: PaymentAttemptType
    status: PaymentAttemptStatus = PaymentAttemptStatus.CREATED
    amount: int | None = None
    currency: str = "INR"
    failure_reason: str | None = None
    attempted_at: datetime | None = None
    completed_at: datetime | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
