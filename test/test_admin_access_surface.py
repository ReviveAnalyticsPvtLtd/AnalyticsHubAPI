from fastapi.routing import APIRoute

from main import app


def test_admin_exposes_exactly_one_user_access_endpoint():
    routes = {
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/admin/users")
        and route.path.endswith("/access")
        for method in route.methods
    }

    assert routes == {("/admin/users/{userId}/access", "PATCH")}
