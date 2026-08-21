import unittest
import sys
import types

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
        def __init__(self, status_code: int = 500, detail=None):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    fastapiStub.HTTPException = _DummyHTTPException
    sys.modules["fastapi"] = fastapiStub

if "api.commons" not in sys.modules:
    commonsStub = types.ModuleType("api.commons")
    commonsStub.client = object()
    sys.modules["api.commons"] = commonsStub

if "requests" not in sys.modules:
    requestsStub = types.ModuleType("requests")

    def _dummy_post(*args, **kwargs):
        class _Resp:
            status_code = 200
            text = ""
        return _Resp()

    requestsStub.post = _dummy_post
    sys.modules["requests"] = requestsStub

from api.services.webhookService import WebhookService


class TestWebhookInvoiceValidation(unittest.TestCase):
    def test_amount_currency_match_passes(self):
        invoice = {"id": "inv_1", "total_amount": 4720, "amount": 4720, "currency": "INR"}
        payment = {"id": "pay_1", "amount": 4720, "currency": "INR"}
        WebhookService._validateFrozenInvoicePaymentMatch(invoice, payment)

    def test_amount_mismatch_raises(self):
        invoice = {"id": "inv_1", "total_amount": 4720, "amount": 4720, "currency": "INR"}
        payment = {"id": "pay_1", "amount": 4700, "currency": "INR"}
        with self.assertRaises(ValueError):
            WebhookService._validateFrozenInvoicePaymentMatch(invoice, payment)

    def test_currency_mismatch_raises(self):
        invoice = {"id": "inv_1", "total_amount": 4720, "amount": 4720, "currency": "INR"}
        payment = {"id": "pay_1", "amount": 4720, "currency": "USD"}
        with self.assertRaises(ValueError):
            WebhookService._validateFrozenInvoicePaymentMatch(invoice, payment)


if __name__ == "__main__":
    unittest.main()
