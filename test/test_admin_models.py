import unittest

from pydantic import ValidationError

from api.adminErrors import AdminApiError, requestValidationErrors
from api.adminModels import AdminLoginRequest, AdminSubscriptionPatch, AdminUserPatch


class AdminModelTests(unittest.TestCase):
    def test_login_requires_valid_email(self):
        with self.assertRaises(ValidationError):
            AdminLoginRequest(email="not-an-email", password="secret")

    def test_user_patch_rejects_unknown_and_empty_payloads(self):
        with self.assertRaises(ValidationError):
            AdminUserPatch.model_validate({})
        with self.assertRaises(ValidationError):
            AdminUserPatch.model_validate({"password": "forbidden"})

    def test_user_patch_distinguishes_omitted_from_explicit_null(self):
        patch = AdminUserPatch.model_validate({"fullName": None})
        self.assertEqual({"fullName"}, patch.model_fields_set)
        with self.assertRaises(ValidationError):
            AdminUserPatch.model_validate({"email": None})
        with self.assertRaises(ValidationError):
            AdminUserPatch.model_validate({"onboarded": None})

    def test_subscription_patch_rejects_unknown_empty_null_and_bad_count(self):
        for payload in (
            {},
            {"billing_mode": "annual"},
            {"status": None},
            {"domain_count": 0},
            {"domain_count": 5},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                AdminSubscriptionPatch.model_validate(payload)

    def test_safe_admin_error_does_not_expose_internal_exception(self):
        error = AdminApiError(422, "Validation failed", {"email": "Invalid email format"})
        self.assertEqual(422, error.statusCode)
        self.assertEqual({"email": "Invalid email format"}, error.errors)
        self.assertNotIn("backend", str(error).lower())

    def test_request_validation_errors_use_body_field_names(self):
        try:
            AdminLoginRequest.model_validate({"email": "bad", "password": 12})
        except ValidationError as exc:
            mapped = requestValidationErrors(exc.errors())
        self.assertIn("email", mapped)
        self.assertIn("password", mapped)


if __name__ == "__main__":
    unittest.main()
