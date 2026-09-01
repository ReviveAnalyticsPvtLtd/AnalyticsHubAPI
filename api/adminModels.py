from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


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


class AdminUserAccessPatch(_StrictModel):
    banned: bool
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def normalizeReason(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


def _normalizeAdminUserIds(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        userId = str(value).strip()
        if not userId or len(userId) > 128:
            raise ValueError("userIds contains an invalid value")
        if userId not in seen:
            seen.add(userId)
            normalized.append(userId)
    return normalized


class AdminUserAccessBatchRequest(_StrictModel):
    userIds: list[str] = Field(min_length=1, max_length=100)
    banned: bool
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("userIds")
    @classmethod
    def normalizeUserIds(cls, values: list[str]) -> list[str]:
        return _normalizeAdminUserIds(values)

    @field_validator("reason", mode="before")
    @classmethod
    def normalizeReason(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class AdminUserAccessBatchResult(_StrictModel):
    userId: str
    outcome: Literal["UPDATED", "FAILED"]
    isBanned: bool | None = None
    bannedAt: str | None = None
    bannedBy: str | None = None
    banReason: str | None = None
    sessionsRevoked: int = Field(ge=0)
    supabaseAuthSynced: bool
    warnings: list[str]
    errorCode: str | None = None


class AdminUserAccessBatchSummary(_StrictModel):
    requested: int = Field(ge=1)
    updated: int = Field(ge=0)
    failed: int = Field(ge=0)
    withWarnings: int = Field(ge=0)


class AdminUserAccessBatchResponse(_StrictModel):
    status: Literal["COMPLETED", "PARTIAL_SUCCESS"]
    summary: AdminUserAccessBatchSummary
    results: list[AdminUserAccessBatchResult]


class AdminUserErasureStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    COMPLETED = "COMPLETED"


class AdminUserErasureRequest(_StrictModel):
    confirmation: Literal["ERASE"]
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def normalizeReason(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class AdminUserErasureAcceptedView(_StrictModel):
    requestId: str
    userId: str
    status: AdminUserErasureStatus
    createdAt: str


class AdminFreeTrialExtensionRequest(_StrictModel):
    userIds: list[str] = Field(min_length=1, max_length=100)
    days: int = Field(ge=1, le=30, strict=True)
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("userIds")
    @classmethod
    def normalizeUserIds(cls, values: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for value in values:
            userId = str(value).strip()
            if not userId:
                raise ValueError("userIds cannot contain blank values")
            if len(userId) > 128:
                raise ValueError("userIds cannot contain values over 128 characters")
            if userId not in seen:
                normalized.append(userId)
                seen.add(userId)
        return normalized

    @field_validator("reason", mode="before")
    @classmethod
    def normalizeReason(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class AdminFreeTrialExtensionSummary(_StrictModel):
    requested: int = Field(ge=1)
    extended: int = Field(ge=0)
    failed: int = Field(ge=0)
    creditSyncPending: int = Field(ge=0)


class AdminFreeTrialExtensionResult(_StrictModel):
    userId: str
    outcome: Literal["EXTENDED", "FAILED"]
    daysAdded: int | None = Field(default=None, ge=1, le=30)
    previousExpiry: str | None = None
    newExpiry: str | None = None
    creditsRefreshed: bool
    creditSyncStatus: Literal[
        "SYNCED", "PENDING", "SUPERSEDED", "CANCELLED", "NOT_APPLICABLE"
    ]
    accessStillBanned: bool
    errorCode: str | None = None


class AdminFreeTrialExtensionResponse(_StrictModel):
    batchId: str
    status: Literal["COMPLETED", "PARTIAL_SUCCESS"]
    days: int = Field(ge=1, le=30)
    summary: AdminFreeTrialExtensionSummary
    results: list[AdminFreeTrialExtensionResult]


class AdminSubscriptionPatch(_StrictModel):
    status: AdminSubscriptionStatus | None = None
    subscribed_experts: str | None = None
    domain_count: int | None = Field(default=None, ge=1, le=4)

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
    isBanned: bool
    bannedAt: str | None = None
    bannedBy: str | None = None
    banReason: str | None = None


class AdminUserAccessView(_StrictModel):
    userId: str
    isBanned: bool
    bannedAt: str | None = None
    bannedBy: str | None = None
    banReason: str | None = None
    sessionsRevoked: int = Field(ge=0)
    supabaseAuthSynced: bool
    warnings: list[str]


class AdminAuditEventView(_StrictModel):
    id: str
    admin_id: str | None = None
    admin_email: str
    session_id: str | None = None
    actor_type: str
    action: str
    target_type: str
    target_id: str | None = None
    changed_fields: str
    details: str
    outcome: str
    created_at: str


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


AdminOverviewPeriod = Literal["7d", "14d", "30d", "90d", "6m", "1y"]
AdminOverviewGranularity = Literal["day", "week", "month"]


class AdminSignupDataset(_StrictModel):
    """One Chart.js-shaped series; the frontend maps this straight to ECharts."""

    label: str
    data: list[int]


class AdminSignupChart(_StrictModel):
    labels: list[str]
    datasets: list[AdminSignupDataset]


class AdminUserSignupOverviewView(_StrictModel):
    period: AdminOverviewPeriod
    granularity: AdminOverviewGranularity
    timezone: str
    rangeStart: str
    rangeEnd: str
    lastUpdatedAt: str
    totalSignups: int
    chart: AdminSignupChart
