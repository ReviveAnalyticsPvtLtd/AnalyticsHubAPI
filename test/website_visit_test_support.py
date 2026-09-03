"""Safety checks shared by opt-in website-visit PostgreSQL tests."""

from urllib.parse import urlparse

from psycopg2.extensions import parse_dsn


_ALLOWED_TEST_DATABASES = {"website_visit_test", "website_visit_migration_test"}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def requireSafeWebsiteVisitTestDatabaseUrl(url: str) -> str:
    parsedUri = urlparse(url)
    if parsedUri.query:
        raise ValueError("WEBSITE_VISIT_TEST_DATABASE_URL must not contain URI query options")
    try:
        parameters = parse_dsn(url)
    except Exception as exc:
        raise ValueError("WEBSITE_VISIT_TEST_DATABASE_URL must be a valid PostgreSQL URI") from exc
    if (
        parsedUri.scheme not in {"postgres", "postgresql"}
        or parameters.get("host") not in _LOOPBACK_HOSTS
        or parameters.get("dbname") not in _ALLOWED_TEST_DATABASES
        or "hostaddr" in parameters
        or "service" in parameters
    ):
        raise ValueError(
            "WEBSITE_VISIT_TEST_DATABASE_URL must name an approved loopback disposable database"
        )
    return url
