import hashlib
import hmac
import os
import sys
import types
import unittest
from unittest.mock import patch

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
    commonsStub.verifyToken = None
    sys.modules["api.commons"] = commonsStub

if "razorpay" not in sys.modules:
    razorpayStub = types.ModuleType("razorpay")

    class _DummyRazorpayClient:
        def __init__(self, *args, **kwargs):
            pass

    razorpayStub.Client = _DummyRazorpayClient
    sys.modules["razorpay"] = razorpayStub

if "redis" not in sys.modules:
    redisStub = types.ModuleType("redis")

    class _DummyRedis:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return None

        def set(self, *args, **kwargs):
            return True

    redisStub.Redis = _DummyRedis
    sys.modules["redis"] = redisStub

if "jose" not in sys.modules:
    joseStub = types.ModuleType("jose")

    class _DummyJwt:
        @staticmethod
        def decode(*args, **kwargs):
            return {}

    joseStub.jwt = _DummyJwt
    sys.modules["jose"] = joseStub

if "requests" not in sys.modules:
    requestsStub = types.ModuleType("requests")

    class _DummyResponse:
        status_code = 200
        text = ""

    def _dummy_post(*args, **kwargs):
        return _DummyResponse()

    requestsStub.post = _dummy_post
    sys.modules["requests"] = requestsStub

from utils.exceptionHandler import CustomException
from api.services.subscriptions.subscriptionService import SubscriptionService


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, name: str, rowsByTable: dict):
        self.name = name
        self.rowsByTable = rowsByTable
        self.filters = {}
        self._limit = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, value):
        self._limit = value
        return self

    def update(self, _payload):
        return self

    def execute(self):
        rows = list(self.rowsByTable.get(self.name, []))
        for field, value in self.filters.items():
            rows = [row for row in rows if row.get(field) == value]
        if self._limit is not None:
            rows = rows[: self._limit]
        return _FakeResponse(rows)


class _FakeClient:
    def __init__(self, rowsByTable: dict):
        self.rowsByTable = rowsByTable

    def table(self, name: str):
        return _FakeTable(name, self.rowsByTable)


class _FakeOrderAPI:
    def __init__(self, paymentsByOrderId: dict | None = None, orderById: dict | None = None):
        self.paymentsByOrderId = paymentsByOrderId or {}
        self.orderById = orderById or {}

    def payments(self, orderId: str):
        return {"items": self.paymentsByOrderId.get(orderId, [])}

    def fetch(self, orderId: str):
        return self.orderById.get(orderId, {"id": orderId, "status": "created", "notes": {}})


class _FakeRazorpayClient:
    def __init__(self, paymentsByOrderId: dict | None = None, orderById: dict | None = None):
        self.order = _FakeOrderAPI(paymentsByOrderId=paymentsByOrderId, orderById=orderById)
        self.payment = type("PaymentAPI", (), {"fetch": staticmethod(lambda _paymentId: {})})()


class TestAnnualRenewalFlow(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("SECRET_KEY", "unit_test_secret")
        os.environ.setdefault("RAZORPAY_KEY_SECRET", "unit_test_rzp_secret")
        self.service = SubscriptionService()

    def test_attempted_order_with_authorized_payment_not_reusable(self):
        self.service.razorpayClient = _FakeRazorpayClient(
            paymentsByOrderId={
                "order_1": [
                    {"id": "pay_1", "status": "authorized"},
                ]
            }
        )
        reusable = self.service._isAnnualRenewalOrderReusable({"id": "order_1", "status": "attempted"})
        self.assertFalse(reusable)

    def test_attempted_order_with_failed_payments_reusable(self):
        self.service.razorpayClient = _FakeRazorpayClient(
            paymentsByOrderId={
                "order_2": [
                    {"id": "pay_2", "status": "failed"},
                ]
            }
        )
        reusable = self.service._isAnnualRenewalOrderReusable({"id": "order_2", "status": "attempted"})
        self.assertTrue(reusable)

    @patch("api.services.subscriptions.subscriptionService.jwt.decode", return_value={"userId": "u1"})
    def test_verify_annual_renewal_rejects_non_renewal_invoice(self, _mockDecode):
        self.service.client = _FakeClient({
            "Invoices": [
                {
                    "id": "inv_1",
                    "userId": "u1",
                    "billing_reason": "initial_purchase",
                    "status": "payment_pending",
                    "total_amount": 100,
                    "amount": 100,
                    "currency": "INR",
                    "razorpay_order_id": "order_1",
                }
            ]
        })
        self.service.razorpayClient = _FakeRazorpayClient()

        orderId = "order_1"
        paymentId = "pay_1"
        signature = hmac.new(
            os.environ["RAZORPAY_KEY_SECRET"].encode(),
            f"{orderId}|{paymentId}".encode(),
            hashlib.sha256,
        ).hexdigest()

        with self.assertRaises(CustomException):
            self.service.verifyAnnualRenewalPayment(
                payload={
                    "invoiceId": "inv_1",
                    "razorpayOrderId": orderId,
                    "razorpayPaymentId": paymentId,
                    "razorpaySignature": signature,
                },
                token="fake_token",
            )

    @patch("api.services.subscriptions.subscriptionService.jwt.decode", return_value={"userId": "u1"})
    def test_verify_subscription_rejects_missing_server_domains(self, _mockDecode):
        orderId = "order_initial_1"
        paymentId = "pay_initial_1"
        signature = hmac.new(
            os.environ["RAZORPAY_KEY_SECRET"].encode(),
            f"{orderId}|{paymentId}".encode(),
            hashlib.sha256,
        ).hexdigest()

        orderApi = _FakeOrderAPI(orderById={
            orderId: {
                "id": orderId,
                "status": "created",
                "notes": {
                    "type": "initial_subscription",
                    "userId": "u1",
                    "billingMode": "monthly_recurring",
                },
            }
        })
        self.service.razorpayClient = type(
            "RazorpayClient",
            (),
            {
                "order": orderApi,
                "payment": type("PaymentAPI", (), {"fetch": staticmethod(lambda _pid: {})})(),
                "token": type("TokenAPI", (), {"fetch": staticmethod(lambda *_args, **_kwargs: {})})(),
            },
        )()

        with self.assertRaises(CustomException):
            self.service.verifySubscription(
                payload={
                    "razorpayOrderId": orderId,
                    "razorpayPaymentId": paymentId,
                    "razorpaySignature": signature,
                },
                token="fake_token",
            )


if __name__ == "__main__":
    unittest.main()
