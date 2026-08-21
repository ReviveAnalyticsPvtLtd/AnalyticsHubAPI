import sys
import types
import unittest

if "logtail" not in sys.modules:
    logtailStub = types.ModuleType("logtail")

    class _DummyLogtailHandler:
        def __init__(self, *args, **kwargs):
            pass

    logtailStub.LogtailHandler = _DummyLogtailHandler
    sys.modules["logtail"] = logtailStub

if "loguru" not in sys.modules:
    loguruStub = types.ModuleType("loguru")

    class _DummyLogger:
        def remove(self, *args, **kwargs):
            return None

        def add(self, *args, **kwargs):
            return None

        def info(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

    loguruStub.logger = _DummyLogger()
    sys.modules["loguru"] = loguruStub

if "fastapi" not in sys.modules:
    fastapiStub = types.ModuleType("fastapi")

    class _DummyHTTPException(Exception):
        pass

    fastapiStub.HTTPException = _DummyHTTPException
    sys.modules["fastapi"] = fastapiStub

if "api.commons" not in sys.modules:
    commonsStub = types.ModuleType("api.commons")
    commonsStub.client = object()
    sys.modules["api.commons"] = commonsStub

if "requests" not in sys.modules:
    requestsStub = types.ModuleType("requests")
    requestsStub.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requestsStub

from api.services.webhookService import WebhookService


class TestWebhookOrderPaid(unittest.TestCase):
    def test_order_paid_routes_to_payment_captured_with_normalized_notes(self):
        service = WebhookService()
        captured = {}

        def _capture(normalizedEvent):
            captured["event"] = normalizedEvent

        service._handlePaymentCaptured = _capture
        service._handleOrderPaid({
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_1",
                        "amount": 100,
                        "currency": "INR",
                        "order_id": "order_1",
                        "notes": [],
                    }
                },
                "order": {
                    "entity": {
                        "id": "order_1",
                        "notes": {
                            "type": "annual_renewal",
                            "invoiceId": "inv_1",
                            "userId": "u1",
                        },
                    }
                },
            }
        })

        normalized = captured["event"]["payload"]["payment"]["entity"]
        self.assertEqual(normalized["id"], "pay_1")
        self.assertEqual(normalized["order_id"], "order_1")
        self.assertEqual(normalized["notes"]["type"], "annual_renewal")
        self.assertEqual(normalized["notes"]["invoiceId"], "inv_1")


if __name__ == "__main__":
    unittest.main()

