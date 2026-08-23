from api.services.subscriptions.subscriptionFieldUtils import (
    CANONICAL_SUBSCRIPTION_SELECT,
    subscriptionErasurePending,
)


def test_erasure_pending_is_in_canonical_subscription_shape():
    assert "erasure_pending" in CANONICAL_SUBSCRIPTION_SELECT.split(", ")


def test_erasure_pending_normalizes_only_explicit_truthy_values():
    assert subscriptionErasurePending({"erasure_pending": True}) is True
    assert subscriptionErasurePending({"erasure_pending": 1}) is True
    assert subscriptionErasurePending({"erasure_pending": False}) is False
    assert subscriptionErasurePending({}) is False
    assert subscriptionErasurePending(None) is False


def test_erasure_pending_string_false_is_not_truthy():
    assert subscriptionErasurePending({"erasure_pending": "false"}) is False
    assert subscriptionErasurePending({"erasure_pending": "true"}) is True
