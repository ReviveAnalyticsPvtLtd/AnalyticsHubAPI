from fastapi import APIRouter, Depends, Header, Query, Request, status

from api.adminModels import (
    AdminAuditEventView,
    AdminFreeTrialExtensionRequest,
    AdminFreeTrialExtensionResponse,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminLogoutResponse,
    AdminSubscriptionPatch,
    AdminSubscriptionView,
    AdminUserAccessBatchRequest,
    AdminUserAccessBatchResponse,
    AdminUserAccessPatch,
    AdminUserAccessView,
    AdminUserErasureAcceptedView,
    AdminUserErasureBatchConfirmRequest,
    AdminUserErasureBatchPreviewRequest,
    AdminUserErasureBatchView,
    AdminUserErasureRequest,
    AdminUserErasureStatusView,
    AdminUserPatch,
    AdminUserView,
)
from api.services.adminAuditService import (
    ADMIN_AUDIT_MAX_PAGE_SIZE,
    AdminAuditService,
    getAdminAuditService,
)
from api.services.adminAuthService import (
    AdminAuthService,
    AdminContext,
    getAdminAuthService,
    resolveAdminClientIp,
    verifyAdmin,
    verifyAdminForLogout,
)
from api.services.adminManagementService import (
    AdminManagementService,
    getAdminManagementService,
)
from api.services.adminTrialExtensionService import (
    AdminTrialExtensionService,
    getAdminTrialExtensionService,
)
from api.services.userErasureService import (
    UserErasureService,
    getUserErasureService,
)
from api.services.userErasureBatchService import (
    UserErasureBatchService,
    getUserErasureBatchService,
)


router = APIRouter()


@router.post("/auth/login", response_model=AdminLoginResponse)
async def login(
    payload: AdminLoginRequest,
    request: Request,
    service: AdminAuthService = Depends(getAdminAuthService),
):
    return service.login(
        str(payload.email), payload.password, resolveAdminClientIp(request)
    )


@router.post("/auth/logout", response_model=AdminLogoutResponse)
async def logout(
    admin: AdminContext = Depends(verifyAdminForLogout),
    service: AdminAuthService = Depends(getAdminAuthService),
):
    service.logout(admin)
    return {"success": True}


@router.get("/users", response_model=list[AdminUserView])
async def listUsers(
    _admin: AdminContext = Depends(verifyAdmin),
    service: AdminManagementService = Depends(getAdminManagementService),
):
    return service.listUsers()


@router.patch(
    "/users/access",
    response_model=AdminUserAccessBatchResponse,
)
def setUsersAccess(
    payload: AdminUserAccessBatchRequest,
    admin: AdminContext = Depends(verifyAdmin),
    service: AdminManagementService = Depends(getAdminManagementService),
):
    return service.setUsersAccess(payload, admin)


@router.post(
    "/user-erasure-batches",
    response_model=AdminUserErasureBatchView,
    status_code=status.HTTP_201_CREATED,
)
def previewUserErasureBatch(
    payload: AdminUserErasureBatchPreviewRequest,
    idempotencyKey: str = Header(alias="Idempotency-Key"),
    admin: AdminContext = Depends(verifyAdmin),
    service: UserErasureBatchService = Depends(getUserErasureBatchService),
):
    return service.preview(payload, idempotencyKey, admin)


@router.post(
    "/user-erasure-batches/{batchId}/confirm",
    response_model=AdminUserErasureBatchView,
    status_code=status.HTTP_202_ACCEPTED,
)
def confirmUserErasureBatch(
    batchId: str,
    payload: AdminUserErasureBatchConfirmRequest,
    admin: AdminContext = Depends(verifyAdmin),
    service: UserErasureBatchService = Depends(getUserErasureBatchService),
):
    return service.confirm(batchId, payload, admin)


@router.get(
    "/user-erasure-batches/{batchId}",
    response_model=AdminUserErasureBatchView,
)
def getUserErasureBatchStatus(
    batchId: str,
    admin: AdminContext = Depends(verifyAdmin),
    service: UserErasureBatchService = Depends(getUserErasureBatchService),
):
    return service.getStatus(batchId, admin)


@router.patch("/users/{userId}", response_model=AdminUserView)
async def updateUser(
    userId: str,
    payload: AdminUserPatch,
    admin: AdminContext = Depends(verifyAdmin),
    service: AdminManagementService = Depends(getAdminManagementService),
):
    return service.updateUser(userId, payload, admin)


@router.patch(
    "/users/{userId}/access",
    response_model=AdminUserAccessView,
)
async def setUserAccess(
    userId: str,
    payload: AdminUserAccessPatch,
    admin: AdminContext = Depends(verifyAdmin),
    service: AdminManagementService = Depends(getAdminManagementService),
):
    return service.setUserAccess(userId, payload, admin)


@router.post(
    "/users/{userId}/erasure",
    response_model=AdminUserErasureAcceptedView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def startUserErasure(
    userId: str,
    payload: AdminUserErasureRequest,
    idempotencyKey: str = Header(alias="Idempotency-Key"),
    admin: AdminContext = Depends(verifyAdmin),
    service: UserErasureService = Depends(getUserErasureService),
):
    return service.start(userId, payload, idempotencyKey, admin)


@router.get(
    "/user-erasure-requests/{requestId}",
    response_model=AdminUserErasureStatusView,
)
async def getUserErasureStatus(
    requestId: str,
    _admin: AdminContext = Depends(verifyAdmin),
    service: UserErasureService = Depends(getUserErasureService),
):
    return service.getStatus(requestId)


@router.post(
    "/free-trial/extensions",
    response_model=AdminFreeTrialExtensionResponse,
)
def extendFreeTrials(
    payload: AdminFreeTrialExtensionRequest,
    idempotencyKey: str = Header(alias="Idempotency-Key"),
    admin: AdminContext = Depends(verifyAdmin),
    service: AdminTrialExtensionService = Depends(getAdminTrialExtensionService),
):
    return service.extend(payload, idempotencyKey, admin)


@router.get("/audit", response_model=list[AdminAuditEventView])
async def listAuditEvents(
    limit: int = Query(default=50, ge=1, le=ADMIN_AUDIT_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    targetType: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    _admin: AdminContext = Depends(verifyAdmin),
    service: AdminAuditService = Depends(getAdminAuditService),
):
    return service.listEvents(
        limit=limit, offset=offset, targetType=targetType, outcome=outcome
    )


@router.get("/subscriptions", response_model=list[AdminSubscriptionView])
async def listSubscriptions(
    _admin: AdminContext = Depends(verifyAdmin),
    service: AdminManagementService = Depends(getAdminManagementService),
):
    return service.listSubscriptions()


@router.patch(
    "/subscriptions/{subscriptionId}", response_model=AdminSubscriptionView
)
async def updateSubscription(
    subscriptionId: str,
    payload: AdminSubscriptionPatch,
    admin: AdminContext = Depends(verifyAdmin),
    service: AdminManagementService = Depends(getAdminManagementService),
):
    return service.updateSubscription(subscriptionId, payload, admin)
