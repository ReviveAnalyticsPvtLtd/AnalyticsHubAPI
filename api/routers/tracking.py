"""Public website visit ingestion route."""

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute

from api.services.websiteVisitService import (
    WebsiteVisitService,
    getWebsiteVisitService,
)
from api.visitModels import WebsiteVisitRequest, WebsiteVisitResponse


class _TrackingRoute(APIRoute):
    """Keep public FastAPI validation shape while never returning raw inputs."""

    def get_route_handler(self):
        originalHandler = super().get_route_handler()

        async def sanitizedValidationHandler(request: Request):
            try:
                return await originalHandler(request)
            except RequestValidationError as exc:
                errors = []
                for error in exc.errors():
                    errors.append({
                        key: value for key, value in error.items()
                        if key not in {"input", "ctx"}
                    })
                return ORJSONResponse(status_code=422, content={"detail": errors})

        return sanitizedValidationHandler


router = APIRouter(route_class=_TrackingRoute)


@router.post("/visit", response_model=WebsiteVisitResponse)
def trackVisit(
    payload: WebsiteVisitRequest,
    request: Request,
    service: WebsiteVisitService = Depends(getWebsiteVisitService),
):
    return service.trackVisit(
        payload,
        request.headers.get("user-agent"),
        request.client.host if request.client else None,
    )
