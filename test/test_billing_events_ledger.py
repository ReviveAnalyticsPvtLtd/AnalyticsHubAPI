import unittest


class _FakeResponse:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class _FakeTable:
    def __init__(self, name, state):
        self.name = name
        self.state = state
        self.payload = None
        self.update_payload = None
        self.filters = []
        self.in_filters = []

    def insert(self, payload):
        self.payload = payload
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def in_(self, field, values):
        self.in_filters.append((field, values))
        return self

    def gte(self, *_args):
        return self

    def lte(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        if self.payload is not None:
            row = {"id": "event_1", **self.payload}
            self.state.setdefault("inserts", {}).setdefault(self.name, []).append(row)
            return _FakeResponse([row])
        if self.update_payload is not None:
            self.state.setdefault("updates", {}).setdefault(self.name, []).append({
                "payload": self.update_payload,
                "filters": list(self.filters),
            })
            return _FakeResponse([self.update_payload])
        return _FakeResponse(self.state.get(self.name, []), count=len(self.state.get(self.name, [])))


class _FakeClient:
    def __init__(self):
        self.state = {}

    def table(self, name):
        return _FakeTable(name, self.state)


class BillingEventServiceTest(unittest.TestCase):
    def test_log_event_writes_structured_audit_row(self):
        from api.services.billing.billingEventService import BillingEventService

        client = _FakeClient()
        service = BillingEventService(client)

        row = service.log_event(
            user_id="user_1",
            event_type="subscription.created",
            event_status="CREATED",
            metadata={"invoiceId": "inv_1"},
        )

        self.assertEqual("billing_events", list(client.state["inserts"].keys())[0])
        self.assertEqual("audit", row["event_category"])
        self.assertEqual("subscription.created", row["event_type"])
        self.assertEqual("CREATED", row["event_status"])
        self.assertEqual({"invoiceId": "inv_1"}, row["metadata_json"])

    def test_create_payment_attempt_writes_machine_safe_payment_row(self):
        from api.services.billing.billingEventService import BillingEventService

        client = _FakeClient()
        service = BillingEventService(client)

        row = service.create_payment_attempt(
            user_id="user_1",
            subscription_id="sub_1",
            invoice_id="inv_1",
            period_start="2026-05-01T00:00:00+00:00",
            period_end="2026-06-01T00:00:00+00:00",
            cycle_key="2026-05",
            amount=236000,
            currency="INR",
            idempotency_key="sub_1:2026-05",
        )

        self.assertEqual("payment_attempt", row["event_category"])
        self.assertEqual("payment.attempt", row["event_type"])
        self.assertEqual("token_debit", row["payment_attempt_type"])
        self.assertEqual("created", row["payment_status"])
        self.assertEqual("created", row["event_status"])
        self.assertEqual(236000, row["amount"])

    def test_update_payment_attempt_updates_payment_columns(self):
        from api.services.billing.billingEventService import BillingEventService

        client = _FakeClient()
        service = BillingEventService(client)

        service.update_payment_attempt(
            event_id="event_1",
            payment_status="captured",
            provider_payment_id="pay_1",
            provider_order_id="order_1",
        )

        update = client.state["updates"]["billing_events"][0]
        self.assertEqual([("id", "event_1")], update["filters"])
        self.assertEqual("captured", update["payload"]["payment_status"])
        self.assertEqual("captured", update["payload"]["event_status"])
        self.assertEqual("pay_1", update["payload"]["provider_payment_id"])
        self.assertIn("completed_at", update["payload"])


if __name__ == "__main__":
    unittest.main()
