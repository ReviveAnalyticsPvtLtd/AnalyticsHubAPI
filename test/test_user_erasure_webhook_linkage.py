from api.services.webhookService import WebhookService


def test_extracts_user_id_from_supported_razorpay_entity_notes():
    event = {
        "payload": {
            "payment": {"entity": {"notes": {"userId": "user-1"}}},
            "order": {"entity": {"notes": {"userId": "user-2"}}},
        }
    }

    assert WebhookService._extractWebhookUserId(event) == "user-1"


def test_linkage_ignores_untrusted_top_level_and_non_string_values():
    assert WebhookService._extractWebhookUserId({"userId": "user-1"}) is None
    assert WebhookService._extractWebhookUserId(
        {"payload": {"payment": {"entity": {"notes": {"userId": 123}}}}}
    ) is None


def test_linkage_accepts_snake_case_note_for_legacy_orders():
    event = {
        "payload": {
            "order": {"entity": {"notes": {"user_id": " user-3 "}}}
        }
    }
    assert WebhookService._extractWebhookUserId(event) == "user-3"
