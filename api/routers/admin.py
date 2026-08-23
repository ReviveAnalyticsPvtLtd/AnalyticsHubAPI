from fastapi import APIRouter, Depends, Query, Request

from api.adminModels import (
    AdminAuditEventView,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminLogoutResponse,
    AdminSubscriptionPatch,
    AdminSubscriptionView,
    AdminUserAccessPatch,
    AdminUserAccessView,
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
