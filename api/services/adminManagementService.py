import datetime
import json

from loguru import logger

from api.adminErrors import AdminApiError
from api.adminModels import AdminSubscriptionPatch, AdminUserPatch
from api.services.adminAuthService import AdminContext
from api.services.subscriptions.subscriptionFieldUtils import (
    mapBillingModeToPlanType,
    normalizeDomainList,
)


ADMIN_USER_FIELDS = (
    "userId", "email", "fullName", "phoneNumber", "profileImage",
    "onboarded", "currentWorkspaceId", "companyName", "role", "profileBio",
    "usage", "industryType", "companySize", "country", "goals", "source",
)
ADMIN_USER_SELECT = ",".join(ADMIN_USER_FIELDS)
ADMIN_SUBSCRIPTION_FIELDS = (
    "id", "user_id", "billing_mode", "current_period_start",
    "current_period_end", "renewal_due_at", "auto_renew_enabled",
    "payment_collection_mode", "status", "default_currency",
    "subscribed_experts", "domain_count", "pending_removals",
    "pending_additions", "billing_state", "razorpay_customer_id",
    "razorpay_token_id", "subscription_anchor_day", "recurring_failures",
    "cancellation_reason", "version", "plan_type", "created_at", "updated_at",
)
ADMIN_SUBSCRIPTION_SELECT = ",".join(ADMIN_SUBSCRIPTION_FIELDS)
ADMIN_SUBSCRIPTION_JSON_FIELDS = (
    "subscribed_experts",
    "pending_removals",
    "pending_additions",
    "billing_state",
)
ADMIN_BATCH_SIZE = 1000


def _serializeUser(row: dict) -> dict:
    result = {field: row.get(field) for field in ADMIN_USER_FIELDS}
    result["onboarded"] = bool(result["onboarded"])
    return result


def _serializeSubscription(row: dict) -> dict:
    result = {field: row.get(field) for field in ADMIN_SUBSCRIPTION_FIELDS}
    for field in ADMIN_SUBSCRIPTION_JSON_FIELDS:
        result[field] = json.dumps(
            result[field], separators=(",", ":"), sort_keys=True
        )
    return result


def _parseSubscribedExperts(rawValue: str) -> list[str]:
    try:
        parsed = json.loads(rawValue)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AdminApiError(
            422,
            "Invalid subscription patch",
            {"subscribed_experts": "Must be a JSON array of non-empty strings"},
        ) from exc

    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise AdminApiError(
            422,
            "Invalid subscription patch",
            {"subscribed_experts": "Must be a JSON array of non-empty strings"},
        )

    experts = []
    seen = set()
    for item in parsed:
        expert = item.strip()
        if not expert:
            raise AdminApiError(
                422,
                "Invalid subscription patch",
                {"subscribed_experts": "Expert names cannot be blank"},
            )
        key = expert.casefold()
        if key not in seen:
            seen.add(key)
            experts.append(expert)

    if len(experts) > 4:
        raise AdminApiError(
            422,
            "Invalid subscription patch",
            {"subscribed_experts": "At most four experts are allowed"},
        )
    return experts


def _escapeIlikeLiteral(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class AdminManagementService:
    def __init__(self, client=None, creditService=None):
        self._client = client
        self._creditService = creditService

    @property
    def client(self):
        if self._client is None:
            from api.commons import client
            self._client = client
        return self._client

    @property
    def creditService(self):
        if self._creditService is None:
            from api.services.credits.creditService import creditService
            self._creditService = creditService
        return self._creditService

    def listUsers(self) -> list[dict]:
        try:
            rows = self._fetchAll("Users", ADMIN_USER_SELECT, "email")
        except Exception as exc:
            logger.error("Admin user list failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to list users") from exc
        users = [_serializeUser(row) for row in rows]
        return sorted(users, key=lambda user: str(user["email"]).casefold())

    def listSubscriptions(self) -> list[dict]:
        try:
            rows = self._fetchAll(
                "subscriptions", ADMIN_SUBSCRIPTION_SELECT, "id"
            )
        except Exception as exc:
            logger.error("Admin subscription list failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to list subscriptions") from exc
        return [_serializeSubscription(row) for row in rows]

    def updateSubscription(
        self,
        subscriptionId: str,
        patch: AdminSubscriptionPatch,
        admin: AdminContext,
    ) -> dict:
        changedFields = sorted(patch.model_fields_set)
        try:
            existingRows = (
                self.client.table("subscriptions")
                .select(ADMIN_SUBSCRIPTION_SELECT)
                .eq("id", subscriptionId)
                .execute().data
            )
        except Exception as exc:
            self._auditSubscriptionUpdate(
                admin, subscriptionId, changedFields, "failed"
            )
            raise AdminApiError(500, "Failed to update subscription") from exc

        if not existingRows:
            self._auditSubscriptionUpdate(
                admin, subscriptionId, changedFields, "not_found"
            )
            raise AdminApiError(404, "Subscription not found")

        current = existingRows[0]
        updatePayload = patch.model_dump(include=patch.model_fields_set)
        expertsSupplied = "subscribed_experts" in patch.model_fields_set
        countSupplied = "domain_count" in patch.model_fields_set

        if expertsSupplied:
            experts = _parseSubscribedExperts(updatePayload["subscribed_experts"])
            derivedCount = len(experts)
            if countSupplied and updatePayload["domain_count"] != derivedCount:
                self._auditSubscriptionUpdate(
                    admin, subscriptionId, changedFields, "invalid"
                )
                raise AdminApiError(
                    422,
                    "Invalid subscription patch",
                    {"domain_count": "Must match the subscribed expert count"},
                )
            updatePayload["subscribed_experts"] = experts
            updatePayload["domain_count"] = derivedCount
        elif countSupplied:
            currentCount = len(normalizeDomainList(current["subscribed_experts"]))
            if updatePayload["domain_count"] != currentCount:
                self._auditSubscriptionUpdate(
                    admin, subscriptionId, changedFields, "invalid"
                )
                raise AdminApiError(
                    422,
                    "Invalid subscription patch",
                    {"domain_count": "Must match the subscribed expert count"},
                )

        if "status" in patch.model_fields_set:
            updatePayload["plan_type"] = mapBillingModeToPlanType(
                current.get("billing_mode"), updatePayload["status"]
            )

        oldVersion = current["version"]
        updatePayload["updated_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        updatePayload["version"] = oldVersion + 1

        try:
            updatedRows = (
                self.client.table("subscriptions")
                .update(updatePayload)
                .eq("id", subscriptionId)
                .eq("version", oldVersion)
                .execute().data
            )
        except Exception as exc:
            self._auditSubscriptionUpdate(
                admin, subscriptionId, changedFields, "failed"
            )
            raise AdminApiError(500, "Failed to update subscription") from exc

        if not updatedRows:
            self._auditSubscriptionUpdate(
                admin, subscriptionId, changedFields, "conflict"
            )
            raise AdminApiError(409, "Subscription changed; reload and try again")

        try:
            if expertsSupplied or countSupplied:
                self.creditService.applyDomainCountChange(
                    userId=current["user_id"],
                    domainCount=updatePayload["domain_count"],
                    grantImmediately=False,
                )
            if "status" in patch.model_fields_set:
                (
                    self.client.table("Sessions")
                    .delete()
                    .eq("userId", current["user_id"])
                    .execute()
                )
        except Exception as exc:
            self._auditSubscriptionUpdate(
                admin, subscriptionId, changedFields, "side_effect_failed"
            )
            raise AdminApiError(500, "Failed to update subscription") from exc

        self._auditSubscriptionUpdate(
            admin, subscriptionId, changedFields, "success"
        )
        return _serializeSubscription(updatedRows[0])

    def updateUser(
        self,
        userId: str,
        patch: AdminUserPatch,
        admin: AdminContext,
    ) -> dict:
        changedFields = sorted(patch.model_fields_set)
        updatePayload = patch.model_dump(include=patch.model_fields_set)
        if "email" in updatePayload:
            updatePayload["email"] = str(updatePayload["email"]).strip().lower()

        try:
            existingRows = (
                self.client.table("Users")
                .select(ADMIN_USER_SELECT)
                .eq("userId", userId)
                .execute().data
            )
        except Exception as exc:
            self._auditUserUpdate(admin, userId, changedFields, "failed")
            raise AdminApiError(500, "Failed to update user") from exc

        if not existingRows:
            self._auditUserUpdate(admin, userId, changedFields, "not_found")
            raise AdminApiError(404, "User not found")

        existingUser = existingRows[0]
        oldEmail = existingUser["email"]
        emailSupplied = "email" in updatePayload
        emailChanged = (
            emailSupplied
            and updatePayload["email"] != str(oldEmail).strip().lower()
        )

        if emailChanged:
            try:
                duplicateRows = (
                    self.client.table("Users")
                    .select("userId")
                    .ilike("email", _escapeIlikeLiteral(updatePayload["email"]))
                    .neq("userId", userId)
                    .execute().data
                )
            except Exception as exc:
                self._auditUserUpdate(admin, userId, changedFields, "failed")
                raise AdminApiError(500, "Failed to update user") from exc
            if duplicateRows:
                self._auditUserUpdate(admin, userId, changedFields, "conflict")
                raise AdminApiError(409, "A user with this email already exists")

            try:
                self.client.auth.admin.update_user_by_id(
                    userId,
                    {"email": updatePayload["email"], "email_confirm": True},
                )
            except Exception as exc:
                self._auditUserUpdate(admin, userId, changedFields, "failed")
                raise AdminApiError(500, "Failed to update user") from exc

        try:
            updatedRows = (
                self.client.table("Users")
                .update(updatePayload)
                .eq("userId", userId)
                .execute().data
            )
            if not updatedRows:
                raise RuntimeError("Users update returned no row")
        except Exception as exc:
            if emailChanged:
                try:
                    self.client.auth.admin.update_user_by_id(
                        userId,
                        {"email": oldEmail, "email_confirm": True},
                    )
                except Exception:
                    self._auditUserUpdate(
                        admin,
                        userId,
                        changedFields,
                        "compensation_failed",
                        critical=True,
                    )
            self._auditUserUpdate(admin, userId, changedFields, "failed")
            raise AdminApiError(500, "Failed to update user") from exc

        if emailSupplied:
            try:
                (
                    self.client.table("Sessions")
                    .delete()
                    .eq("userId", userId)
                    .execute()
                )
            except Exception as exc:
                self._auditUserUpdate(
                    admin, userId, changedFields, "side_effect_failed"
                )
                raise AdminApiError(500, "Failed to update user") from exc

        self._auditUserUpdate(admin, userId, changedFields, "success")
        return _serializeUser(updatedRows[0])

    def _fetchAll(
        self,
        tableName: str,
        selectFields: str,
        orderColumn: str,
    ) -> list[dict]:
        rows = []
        start = 0
        while True:
            batch = (
                self.client.table(tableName)
                .select(selectFields)
                .order(orderColumn)
                .range(start, start + ADMIN_BATCH_SIZE - 1)
                .execute().data
            ) or []
            rows.extend(batch)
            if len(batch) < ADMIN_BATCH_SIZE:
                return rows
            start += ADMIN_BATCH_SIZE

    @staticmethod
    def _auditUserUpdate(
        admin: AdminContext,
        userId: str,
        changedFields: list[str],
        outcome: str,
        critical: bool = False,
    ) -> None:
        event = logger.bind(
            adminId=admin.adminId,
            sessionId=admin.sessionId,
            targetType="user",
            targetId=userId,
            changedFields=changedFields,
            outcome=outcome,
        )
        if critical:
            event.critical("admin_audit")
        else:
            event.info("admin_audit")

    @staticmethod
    def _auditSubscriptionUpdate(
        admin: AdminContext,
        subscriptionId: str,
        changedFields: list[str],
        outcome: str,
    ) -> None:
        logger.bind(
            adminId=admin.adminId,
            sessionId=admin.sessionId,
            targetType="subscription",
            targetId=subscriptionId,
            changedFields=changedFields,
            outcome=outcome,
        ).info("admin_audit")


_adminManagementService: AdminManagementService | None = None


def getAdminManagementService() -> AdminManagementService:
    global _adminManagementService
    if _adminManagementService is None:
        _adminManagementService = AdminManagementService()
    return _adminManagementService
