"""PostgreSQL persistence for administrator user-erasure batch previews."""

__all__ = [
    "UserErasureBatchRepository",
    "getUserErasureBatchRepository",
]


import os
import uuid

import psycopg2
from psycopg2.extras import RealDictCursor

from api.adminErrors import AdminApiError
from api.services.userErasureRepository import ERASURE_STEP_NAMES


BATCH_SELECT = """
    id, requested_by, idempotency_key, request_hash, status, reason,
    requested_count, ready_count, expires_at, created_at, updated_at,
    confirmed_at, completed_at
"""

ITEM_SELECT = """
    item.id, item.batch_id, item.ordinal, item.target_user_id,
    item.subject_fingerprint, item.classification, item.request_id,
    item.error_code, item.created_at, item.updated_at,
    request.status as request_status,
    request.created_at as request_created_at,
    request.updated_at as request_updated_at,
    request.started_at as request_started_at,
    request.completed_at as request_completed_at
"""

ACTIVE_REQUEST_STATUSES = frozenset({
    "PENDING",
    "IN_PROGRESS",
    "PARTIALLY_FAILED",
})


def _defaultConnection():
    databaseUrl = os.environ.get("DATABASE_URL")
    if not databaseUrl:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(
        databaseUrl,
        application_name="nubrix-user-erasure-batch",
    )


class UserErasureBatchRepository:
    def __init__(self, connectionFactory=None):
        self.connectionFactory = connectionFactory or _defaultConnection

    def findByIdempotency(self, idempotencyKey: str) -> dict | None:
        return self._loadBatch("batch.idempotency_key = %s", idempotencyKey)

    def createPreview(
        self,
        userItems: list[dict],
        adminId: str,
        idempotencyKey: str,
        requestHash: str,
        reason: str | None,
    ) -> dict:
        if not 1 <= len(userItems) <= 25:
            raise ValueError("userItems must contain between 1 and 25 subjects")

        userIds = [item["userId"] for item in userItems]
        fingerprints = [item["subjectFingerprint"] for item in userItems]
        connection = self.connectionFactory()
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    'select "userId" from public."Users" '
                    'where "userId" = any(%s)',
                    (userIds,),
                )
                existingUserIds = {
                    row["userId"] for row in cursor.fetchall()
                }

                cursor.execute(
                    """
                    select id, target_user_id, subject_fingerprint, status
                    from public.user_erasure_requests
                    where target_user_id = any(%s)
                       or subject_fingerprint = any(%s)
                    order by created_at desc
                    """,
                    (userIds, fingerprints),
                )
                history = [dict(row) for row in cursor.fetchall()]
                activeByUserId = {}
                completedByFingerprint = {}
                for request in history:
                    status = str(request.get("status") or "")
                    targetUserId = request.get("target_user_id")
                    subjectFingerprint = request.get("subject_fingerprint")
                    if status in ACTIVE_REQUEST_STATUSES and targetUserId:
                        activeByUserId.setdefault(targetUserId, request)
                    elif status == "COMPLETED" and subjectFingerprint:
                        completedByFingerprint.setdefault(
                            subjectFingerprint, request
                        )

                items = []
                readyCount = 0
                for userItem in userItems:
                    userId = userItem["userId"]
                    subjectFingerprint = userItem["subjectFingerprint"]
                    activeRequest = activeByUserId.get(userId)
                    completedRequest = completedByFingerprint.get(
                        subjectFingerprint
                    )
                    if activeRequest is not None:
                        classification = "ALREADY_IN_PROGRESS"
                        requestId = activeRequest["id"]
                        errorCode = None
                    elif completedRequest is not None:
                        classification = "ALREADY_COMPLETED"
                        requestId = completedRequest["id"]
                        errorCode = None
                    elif userId in existingUserIds:
                        classification = "READY"
                        requestId = None
                        errorCode = None
                        readyCount += 1
                    else:
                        classification = "USER_NOT_FOUND"
                        requestId = None
                        errorCode = "USER_NOT_FOUND"
                    items.append({
                        "id": str(uuid.uuid4()),
                        "ordinal": userItem["ordinal"],
                        "target_user_id": userId,
                        "subject_fingerprint": subjectFingerprint,
                        "classification": classification,
                        "request_id": requestId,
                        "error_code": errorCode,
                    })

                cursor.execute(
                    f"""
                    insert into public.user_erasure_batches (
                        requested_by, idempotency_key, request_hash, reason,
                        requested_count, ready_count, expires_at
                    )
                    values (
                        %s, %s, %s, %s, %s, %s,
                        now() + interval '15 minutes'
                    )
                    returning {BATCH_SELECT}
                    """,
                    (
                        adminId,
                        idempotencyKey,
                        requestHash,
                        reason,
                        len(userItems),
                        readyCount,
                    ),
                )
                batch = dict(cursor.fetchone())
                batchId = batch["id"]
                cursor.executemany(
                    """
                    insert into public.user_erasure_batch_items (
                        id, batch_id, ordinal, target_user_id,
                        subject_fingerprint, classification, request_id,
                        error_code
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            item["id"],
                            batchId,
                            item["ordinal"],
                            item["target_user_id"],
                            item["subject_fingerprint"],
                            item["classification"],
                            item["request_id"],
                            item["error_code"],
                        )
                        for item in items
                    ],
                )
            connection.commit()
            batch["items"] = [
                {**item, "batch_id": batchId}
                for item in sorted(items, key=lambda item: item["ordinal"])
            ]
            return batch
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def getBatch(self, batchId: str) -> dict | None:
        return self._loadBatch("batch.id = %s", batchId)

    def listReconcilable(self, limit: int = 100) -> list[str]:
        boundedLimit = max(1, min(int(limit), 100))
        connection = self.connectionFactory()
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    select batch.id
                    from public.user_erasure_batches as batch
                    where (
                        batch.status = 'PREVIEWED'
                        and batch.expires_at <= now()
                    ) or (
                        batch.status in ('IN_PROGRESS', 'PARTIALLY_FAILED')
                        and batch.confirmed_at is not null
                    )
                    order by batch.created_at
                    limit %s
                    """,
                    (boundedLimit,),
                )
                return [str(row["id"]) for row in cursor.fetchall()]
        finally:
            connection.close()

    def reconcileBatch(self, batchId: str) -> dict | None:
        connection = self.connectionFactory()
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    f"""
                    select {BATCH_SELECT},
                           (batch.expires_at <= now()) as is_expired
                    from public.user_erasure_batches as batch
                    where batch.id = %s
                    limit 1
                    for update
                    """,
                    (batchId,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                batch = dict(row)
                storedStatus = str(batch.get("status") or "")

                cursor.execute(
                    """
                    update public.user_erasure_batch_items as item
                    set target_user_id = null, updated_at = now()
                    from public.user_erasure_requests as request
                    where item.batch_id = %s
                      and item.request_id = request.id
                      and request.status = 'COMPLETED'
                      and item.target_user_id is not null
                    """,
                    (batchId,),
                )
                items = self._loadItems(cursor, batchId)

                if storedStatus == "PREVIEWED":
                    nextStatus = (
                        "EXPIRED" if bool(batch.get("is_expired")) else "PREVIEWED"
                    )
                elif storedStatus in {"IN_PROGRESS", "PARTIALLY_FAILED"}:
                    childStatuses = []
                    for item in items:
                        if item.get("request_id") is None:
                            continue
                        childStatuses.append(
                            str(item.get("request_status") or "PENDING")
                        )
                    if "PARTIALLY_FAILED" in childStatuses:
                        nextStatus = "PARTIALLY_FAILED"
                    elif any(
                        status in {"PENDING", "IN_PROGRESS"}
                        for status in childStatuses
                    ):
                        nextStatus = "IN_PROGRESS"
                    else:
                        nextStatus = "COMPLETED"
                else:
                    nextStatus = storedStatus

                terminal = nextStatus in {"COMPLETED", "EXPIRED"}
                if terminal:
                    cursor.execute(
                        """
                        update public.user_erasure_batch_items
                        set target_user_id = null, updated_at = now()
                        where batch_id = %s and target_user_id is not null
                        """,
                        (batchId,),
                    )

                cursor.execute(
                    f"""
                    update public.user_erasure_batches
                    set status = %s,
                        reason = case when %s then null else reason end,
                        completed_at = case
                            when %s then coalesce(completed_at, now())
                            else completed_at
                        end,
                        updated_at = now()
                    where id = %s
                    returning {BATCH_SELECT}
                    """,
                    (nextStatus, terminal, terminal, batchId),
                )
                updated = cursor.fetchone()
                if updated is None:
                    raise RuntimeError("user erasure batch reconciliation failed")
                batch = dict(updated)
                batch["items"] = self._loadItems(cursor, batchId)
            connection.commit()
            return batch
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def confirmBatch(
        self,
        batchId: str,
        adminId: str,
        sessionId: str,
        confirmation: str,
    ) -> dict:
        connection = self.connectionFactory()
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    f"""
                    select {BATCH_SELECT},
                           (batch.expires_at <= now()) as is_expired
                    from public.user_erasure_batches as batch
                    where batch.id = %s
                    limit 1
                    for update
                    """,
                    (batchId,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise AdminApiError(404, "User erasure batch not found")
                batch = dict(row)

                if str(batch.get("requested_by")) != str(adminId):
                    raise AdminApiError(
                        403,
                        "Only the administrator who created the preview can confirm it",
                    )

                status = str(batch.get("status") or "")
                if status in {"IN_PROGRESS", "PARTIALLY_FAILED", "COMPLETED"}:
                    batch["items"] = self._loadItems(cursor, batchId)
                    connection.commit()
                    return batch
                if status == "EXPIRED" or (
                    status == "PREVIEWED" and bool(batch.get("is_expired"))
                ):
                    raise AdminApiError(
                        409, "User erasure batch preview has expired"
                    )
                if status != "PREVIEWED":
                    raise AdminApiError(
                        409, "User erasure batch cannot be confirmed"
                    )

                readyCount = int(batch.get("ready_count") or 0)
                if readyCount == 0:
                    raise AdminApiError(
                        409, "User erasure batch has no ready users"
                    )
                expectedConfirmation = (
                    "ERASE 1 USER"
                    if readyCount == 1
                    else f"ERASE {readyCount} USERS"
                )
                if confirmation != expectedConfirmation:
                    raise AdminApiError(
                        422,
                        "Confirmation does not match the reviewed user count",
                    )

                cursor.execute(
                    """
                    select created_at
                    from public.admin_sessions
                    where id = %s
                      and admin_id = %s
                      and revoked_at is null
                      and expires_at > now()
                      and created_at >= now() - interval '10 minutes'
                    limit 1
                    for share
                    """,
                    (sessionId, adminId),
                )
                if cursor.fetchone() is None:
                    raise AdminApiError(
                        403,
                        "A recent administrator login is required to confirm user erasure",
                    )

                items = self._loadItems(cursor, batchId, forUpdate=True)
                readyItems = [
                    item
                    for item in items
                    if item.get("classification") == "READY"
                ]
                if len(readyItems) != readyCount:
                    raise RuntimeError("erasure batch ready count is inconsistent")

                userIds = sorted({
                    str(item["target_user_id"])
                    for item in readyItems
                    if item.get("target_user_id") is not None
                })
                for userId in userIds:
                    cursor.execute(
                        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (userId,),
                    )

                fingerprints = [
                    str(item["subject_fingerprint"]) for item in readyItems
                ]
                cursor.execute(
                    'select "userId" from public."Users" '
                    'where "userId" = any(%s) for update',
                    (userIds,),
                )
                existingUserIds = {
                    str(user["userId"]) for user in cursor.fetchall()
                }
                cursor.execute(
                    """
                    select id, target_user_id, subject_fingerprint, status
                    from public.user_erasure_requests
                    where target_user_id = any(%s)
                       or subject_fingerprint = any(%s)
                    order by created_at desc
                    """,
                    (userIds, fingerprints),
                )
                activeByUserId = {}
                completedByFingerprint = {}
                for requestRow in cursor.fetchall():
                    request = dict(requestRow)
                    requestStatus = str(request.get("status") or "")
                    targetUserId = request.get("target_user_id")
                    subjectFingerprint = request.get("subject_fingerprint")
                    if requestStatus in ACTIVE_REQUEST_STATUSES and targetUserId:
                        activeByUserId.setdefault(str(targetUserId), request)
                    elif requestStatus == "COMPLETED" and subjectFingerprint:
                        completedByFingerprint.setdefault(
                            str(subjectFingerprint), request
                        )

                for item in readyItems:
                    userId = (
                        str(item["target_user_id"])
                        if item.get("target_user_id") is not None
                        else None
                    )
                    fingerprint = str(item["subject_fingerprint"])
                    activeRequest = activeByUserId.get(userId)
                    completedRequest = completedByFingerprint.get(fingerprint)
                    if activeRequest is not None:
                        self._updateItem(
                            cursor,
                            item["id"],
                            "ALREADY_IN_PROGRESS",
                            activeRequest["id"],
                            None,
                        )
                    elif completedRequest is not None:
                        self._updateItem(
                            cursor,
                            item["id"],
                            "ALREADY_COMPLETED",
                            completedRequest["id"],
                            None,
                        )
                    elif userId is None or userId not in existingUserIds:
                        self._updateItem(
                            cursor,
                            item["id"],
                            "USER_NOT_FOUND",
                            None,
                            "USER_NOT_FOUND",
                        )
                    else:
                        request = self._createChildRequest(
                            cursor=cursor,
                            batchId=batchId,
                            item=item,
                            userId=userId,
                            adminId=adminId,
                            reason=batch.get("reason"),
                        )
                        self._updateItem(
                            cursor,
                            item["id"],
                            "READY",
                            request["id"],
                            None,
                        )

                cursor.execute(
                    f"""
                    update public.user_erasure_batches
                    set status = 'IN_PROGRESS', confirmed_at = now(),
                        updated_at = now()
                    where id = %s and status = 'PREVIEWED'
                    returning {BATCH_SELECT}
                    """,
                    (batchId,),
                )
                updated = cursor.fetchone()
                if updated is None:
                    raise RuntimeError("erasure batch confirmation update failed")
                batch = dict(updated)
                batch["items"] = self._loadItems(cursor, batchId)
            connection.commit()
            return batch
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _loadItems(cursor, batchId: str, forUpdate: bool = False) -> list[dict]:
        lockClause = "for update of item" if forUpdate else ""
        cursor.execute(
            f"""
            select {ITEM_SELECT}
            from public.user_erasure_batch_items as item
            left join public.user_erasure_requests as request
              on request.id = item.request_id
            where item.batch_id = %s
            order by item.ordinal
            {lockClause}
            """,
            (batchId,),
        )
        return [dict(item) for item in cursor.fetchall()]

    @staticmethod
    def _updateItem(
        cursor,
        itemId: str,
        classification: str,
        requestId: str | None,
        errorCode: str | None,
    ) -> None:
        cursor.execute(
            """
            update public.user_erasure_batch_items
            set classification = %s, request_id = %s, error_code = %s,
                updated_at = now()
            where id = %s
            """,
            (classification, requestId, errorCode, itemId),
        )

    @staticmethod
    def _createChildRequest(
        cursor,
        batchId: str,
        item: dict,
        userId: str,
        adminId: str,
        reason: str | None,
    ) -> dict:
        cursor.execute(
            """
            update public."Users"
            set "isBanned" = true,
                "bannedAt" = case
                    when not "isBanned" or "bannedAt" is null then now()
                    else "bannedAt"
                end,
                "bannedBy" = case
                    when not "isBanned" or "bannedBy" is null then %s
                    else "bannedBy"
                end,
                "banReason" = case
                    when not "isBanned" then %s
                    else coalesce(%s, "banReason")
                end
            where "userId" = %s
            """,
            (adminId, reason, reason, userId),
        )
        if int(cursor.rowcount or 0) != 1:
            raise RuntimeError("erasure subject disappeared while locked")
        cursor.execute(
            'delete from public."Sessions" where "userId" = %s',
            (userId,),
        )
        cursor.execute(
            """
            update public.subscriptions
            set erasure_pending = true,
                auto_renew_enabled = false,
                updated_at = now()
            where user_id = %s
            """,
            (userId,),
        )
        cursor.execute(
            """
            update public.admin_free_trial_extension_items
            set credit_sync_status = 'CANCELLED', updated_at = now()
            where user_id = %s and credit_sync_status = 'PENDING'
            """,
            (userId,),
        )

        childKey = str(
            uuid.uuid5(uuid.UUID(str(batchId)), str(item["id"]))
        )
        cursor.execute(
            """
            insert into public.user_erasure_requests (
                target_user_id, subject_fingerprint, requested_by,
                idempotency_key, reason
            )
            values (%s, %s, %s, %s, %s)
            returning id, status, created_at
            """,
            (
                userId,
                item["subject_fingerprint"],
                adminId,
                childKey,
                reason,
            ),
        )
        request = dict(cursor.fetchone())
        cursor.executemany(
            """
            insert into public.user_erasure_steps (request_id, step_name)
            values (%s, %s)
            on conflict (request_id, step_name) do nothing
            """,
            [(request["id"], stepName) for stepName in ERASURE_STEP_NAMES],
        )
        return request

    def _loadBatch(self, predicate: str, value: str) -> dict | None:
        connection = self.connectionFactory()
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    f"""
                    select {BATCH_SELECT}
                    from public.user_erasure_batches as batch
                    where {predicate}
                    limit 1
                    """,
                    (value,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                batch = dict(row)
                cursor.execute(
                    f"""
                    select {ITEM_SELECT}
                    from public.user_erasure_batch_items as item
                    left join public.user_erasure_requests as request
                      on request.id = item.request_id
                    where item.batch_id = %s
                    order by item.ordinal
                    """,
                    (batch["id"],),
                )
                batch["items"] = [dict(item) for item in cursor.fetchall()]
                return batch
        finally:
            connection.close()


_userErasureBatchRepository: UserErasureBatchRepository | None = None


def getUserErasureBatchRepository() -> UserErasureBatchRepository:
    global _userErasureBatchRepository
    if _userErasureBatchRepository is None:
        _userErasureBatchRepository = UserErasureBatchRepository()
    return _userErasureBatchRepository
