"""Atomic PostgreSQL access restoration coordinated with user erasure."""

__all__ = [
    "AdminUserAccessRepository",
    "AdminUserAccessRestoreError",
    "getAdminUserAccessRepository",
]


import os

import psycopg2
from psycopg2.extras import RealDictCursor

from api.adminErrors import AdminApiError


ACCESS_SELECT = '"userId", "isBanned", "bannedAt", "bannedBy", "banReason"'


class AdminUserAccessRestoreError(Exception):
    def __init__(self, stage: str):
        self.stage = stage
        super().__init__(stage)


def _defaultConnection():
    databaseUrl = os.environ.get("DATABASE_URL")
    if not databaseUrl:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(
        databaseUrl,
        application_name="nubrix-admin-user-access",
    )


class AdminUserAccessRepository:
    def __init__(self, connectionFactory=None):
        self.connectionFactory = connectionFactory or _defaultConnection

    def restoreUserAccess(self, userId: str) -> dict:
        connection = self.connectionFactory()
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (userId,),
                )
                cursor.execute(
                    f"""
                    select {ACCESS_SELECT}
                    from public."Users"
                    where "userId" = %s
                    limit 1
                    for update
                    """,
                    (userId,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise AdminApiError(404, "User not found")
                user = dict(row)

                cursor.execute(
                    """
                    select 1 as erasure_pending
                    from public.subscriptions
                    where user_id = %s and erasure_pending = true
                    limit 1
                    for share
                    """,
                    (userId,),
                )
                erasurePending = cursor.fetchone() is not None
                cursor.execute(
                    """
                    select id
                    from public.user_erasure_requests
                    where target_user_id = %s and status <> 'COMPLETED'
                    limit 1
                    for share
                    """,
                    (userId,),
                )
                activeRequest = cursor.fetchone() is not None
                if erasurePending or activeRequest:
                    raise AdminApiError(409, "User erasure is in progress")

                sessionsRevoked = 0
                if bool(user.get("isBanned")):
                    try:
                        cursor.execute(
                            'delete from public."Sessions" where "userId" = %s',
                            (userId,),
                        )
                    except Exception as exc:
                        raise AdminUserAccessRestoreError(
                            "session_revocation"
                        ) from exc
                    sessionsRevoked = int(cursor.rowcount or 0)
                    try:
                        cursor.execute(
                            f"""
                            update public."Users"
                            set "isBanned" = false,
                                "bannedAt" = null,
                                "bannedBy" = null,
                                "banReason" = null
                            where "userId" = %s
                            returning {ACCESS_SELECT}
                            """,
                            (userId,),
                        )
                    except Exception as exc:
                        raise AdminUserAccessRestoreError(
                            "access_update"
                        ) from exc
                    updated = cursor.fetchone()
                    if updated is None:
                        raise AdminUserAccessRestoreError("access_update")
                    user = dict(updated)
            connection.commit()
            return {**user, "sessionsRevoked": sessionsRevoked}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


_adminUserAccessRepository: AdminUserAccessRepository | None = None


def getAdminUserAccessRepository() -> AdminUserAccessRepository:
    global _adminUserAccessRepository
    if _adminUserAccessRepository is None:
        _adminUserAccessRepository = AdminUserAccessRepository()
    return _adminUserAccessRepository
