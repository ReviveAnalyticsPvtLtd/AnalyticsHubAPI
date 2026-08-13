from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


AdminSubscriptionStatus = Literal[
    "none", "trial", "active", "renewal_upcoming", "payment_pending",
    "past_due", "suspended", "paused", "cancelled", "expired",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminLoginRequest(_StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AdminPublic(_StrictModel):
    id: str
    email: str
    name: str


class AdminLoginResponse(_StrictModel):
    token: str
    admin: AdminPublic


class AdminLogoutResponse(_StrictModel):
    success: bool


class AdminUserPatch(_StrictModel):
    email: EmailStr | None = None
    fullName: str | None = None
    phoneNumber: str | None = None
    onboarded: bool | None = None
    companyName: str | None = None
    role: str | None = None
    country: str | None = None
    goals: str | None = None

    @model_validator(mode="after")
    def validatePatch(self):
        if not self.model_fields_set:
            raise ValueError("At least one editable field is required")
        if "email" in self.model_fields_set and self.email is None:
            raise ValueError("email cannot be null")
        if "onboarded" in self.model_fields_set and self.onboarded is None:
            raise ValueError("onboarded cannot be null")
        return self


class AdminSubscriptionPatch(_StrictModel):
    status: AdminSubscriptionStatus | None = None
    subscribed_experts: str | None = None
    domain_count: int | None = Field(default=None, ge=0, le=4)

    @model_validator(mode="after")
    def validatePatch(self):
        if not self.model_fields_set:
            raise ValueError("At least one editable field is required")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AdminUserView(_StrictModel):
    userId: str
    email: str
    fullName: str | None = None
    phoneNumber: str | None = None
    profileImage: str | None = None
    onboarded: bool
    currentWorkspaceId: str | None = None
    companyName: str | None = None
    role: str | None = None
    profileBio: str | None = None
    usage: str | None = None
    industryType: str | None = None
    companySize: str | None = None
    country: str | None = None
    goals: str | None = None
    source: str | None = None


class AdminSubscriptionView(_StrictModel):
    id: str
    user_id: str
    billing_mode: str
    current_period_start: str | None = None
    current_period_end: str | None = None
    renewal_due_at: str | None = None
    auto_renew_enabled: bool
    payment_collection_mode: str
    status: str
    default_currency: str
    subscribed_experts: str
    domain_count: int
    pending_removals: str
    pending_additions: str
    billing_state: str
    razorpay_customer_id: str | None = None
    razorpay_token_id: str | None = None
    subscription_anchor_day: int | None = None
    recurring_failures: int
    cancellation_reason: str | None = None
    version: int
    plan_type: str
    created_at: str
    updated_at: str
