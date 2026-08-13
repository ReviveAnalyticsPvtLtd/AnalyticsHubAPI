from fastapi import APIRouter, Depends, Request

from api.adminModels import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminLogoutResponse,
    AdminSubscriptionPatch,
    AdminSubscriptionView,
    AdminUserPatch,
    AdminUserView,
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
