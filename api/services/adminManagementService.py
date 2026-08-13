from loguru import logger

from api.adminErrors import AdminApiError
from api.adminModels import AdminUserPatch
from api.services.adminAuthService import AdminContext


ADMIN_USER_FIELDS = (
    "userId", "email", "fullName", "phoneNumber", "profileImage",
    "onboarded", "currentWorkspaceId", "companyName", "role", "profileBio",
    "usage", "industryType", "companySize", "country", "goals", "source",
)
ADMIN_USER_SELECT = ",".join(ADMIN_USER_FIELDS)
ADMIN_BATCH_SIZE = 1000


def _serializeUser(row: dict) -> dict:
    result = {field: row.get(field) for field in ADMIN_USER_FIELDS}
    result["onboarded"] = bool(result["onboarded"])
    return result


def _escapeIlikeLiteral(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class AdminManagementService:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from api.commons import client
            self._client = client
        return self._client

    def listUsers(self) -> list[dict]:
        try:
            rows = self._fetchAll("Users", ADMIN_USER_SELECT, "email")
        except Exception as exc:
            logger.error("Admin user list failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to list users") from exc
        users = [_serializeUser(row) for row in rows]
        return sorted(users, key=lambda user: str(user["email"]).casefold())

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


_adminManagementService: AdminManagementService | None = None


def getAdminManagementService() -> AdminManagementService:
    global _adminManagementService
    if _adminManagementService is None:
        _adminManagementService = AdminManagementService()
    return _adminManagementService
