"""
billingEventService.py

Small billing package helper for the unified billing_events ledger.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["BillingEventService"]


import datetime


_TERMINAL_PAYMENT_STATUSES = {
    "captured",
    "failed",
    "expired",
    "cancelled",
    "precheck_failed",
    "investigated",
}


class BillingEventService:
    """
    Writes audit events, notifications, reconciliation events, and payment
    attempts to the unified ``billing_events`` table.
    """

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def log_event(
        self,
        user_id: str | None,
        event_type: str,
        event_status: str | None = None,
        category: str = "audit",
        metadata: dict | None = None,
        idempotency_key: str | None = None,
        subscription_id: str | None = None,
        invoice_id: str | None = None,
        webhook_event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> dict | None:
        payload = {
            "user_id": user_id,
            "subscription_id": subscription_id,
            "invoice_id": invoice_id,
            "webhook_event_id": webhook_event_id,
            "event_category": category,
            "event_type": event_type,
            "event_status": event_status,
            "metadata_json": metadata or {},
            "occurred_at": occurred_at or self._now(),
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key

        result = self.client.table("billing_events").insert(payload).execute()
        return result.data[0] if result.data else None

    def create_payment_attempt(
        self,
        user_id: str,
        subscription_id: str | None = None,
        invoice_id: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        cycle_key: str | None = None,
        amount: int | None = None,
        currency: str = "INR",
        idempotency_key: str | None = None,
        payment_attempt_type: str = "token_debit",
        payment_status: str = "created",
        provider: str = "razorpay",
        provider_order_id: str | None = None,
        provider_payment_id: str | None = None,
        provider_invoice_id: str | None = None,
        provider_payment_link_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        now = self._now()
        payload = {
            "user_id": user_id,
            "subscription_id": subscription_id,
            "invoice_id": invoice_id,
            "event_category": "payment_attempt",
            "event_type": "payment.attempt",
            "event_status": payment_status,
            "payment_attempt_type": payment_attempt_type,
            "payment_status": payment_status,
            "provider": provider,
            "provider_payment_id": provider_payment_id,
            "provider_order_id": provider_order_id,
            "provider_invoice_id": provider_invoice_id,
            "provider_payment_link_id": provider_payment_link_id,
            "period_start": period_start,
            "period_end": period_end,
            "cycle_key": cycle_key,
            "amount": amount,
            "currency": currency,
            "idempotency_key": idempotency_key,
            "metadata_json": metadata or {},
            "attempted_at": now,
            "occurred_at": now,
        }

        result = self.client.table("billing_events").insert(payload).execute()
        return result.data[0] if result.data else None

    def update_payment_attempt(
        self,
        event_id: str,
        payment_status: str,
        provider_payment_id: str | None = None,
        provider_order_id: str | None = None,
        provider_invoice_id: str | None = None,
        provider_payment_link_id: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        payload = {
            "payment_status": payment_status,
            "event_status": payment_status,
        }
        if provider_payment_id:
            payload["provider_payment_id"] = provider_payment_id
        if provider_order_id:
            payload["provider_order_id"] = provider_order_id
        if provider_invoice_id:
            payload["provider_invoice_id"] = provider_invoice_id
        if provider_payment_link_id:
            payload["provider_payment_link_id"] = provider_payment_link_id
        if failure_reason:
            payload["failure_reason"] = failure_reason[:2000]
        if payment_status in _TERMINAL_PAYMENT_STATUSES:
            payload["completed_at"] = self._now()

        self.client.table("billing_events").update(payload).eq("id", event_id).execute()

    def count_events(self, event_type: str, window_start: str) -> int:
        result = (
            self.client.table("billing_events")
            .select("id", count="exact")
            .eq("event_type", event_type)
            .gte("occurred_at", window_start)
            .execute()
        )
        return result.count if hasattr(result, "count") and result.count is not None else len(result.data)

    def find_unresolved_attempts(
        self,
        cutoff: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        query = (
            self.client.table("billing_events")
            .select(
                "id, user_id, subscription_id, event_status, payment_status, "
                "amount, currency, attempted_at, provider_payment_id, "
                "provider_order_id, cycle_key"
            )
            .eq("event_category", "payment_attempt")
            .in_("payment_status", ["created", "pending_provider_ack", "authorized"])
        )
        if cutoff:
            query = query.lte("attempted_at", cutoff)
        if limit:
            query = query.limit(limit)
        return query.execute().data
