import pytest

from test.website_visit_test_support import requireSafeWebsiteVisitTestDatabaseUrl


@pytest.mark.parametrize("url", [
    "postgresql://visit_test_owner@db.example.com:55439/website_visit_test",
    "postgresql://visit_test_owner@127.0.0.1:55439/postgres",
    "postgresql://visit_test_owner@127.0.0.1:55439/analytics_production",
    "postgresql://visit_test_owner@127.0.0.1:55439/website_visit_test?host=db.example.com",
    "not-a-database-url",
])
def test_disposable_database_guard_rejects_non_loopback_or_unapproved_database(url):
    with pytest.raises(ValueError):
        requireSafeWebsiteVisitTestDatabaseUrl(url)


def test_disposable_database_guard_allows_only_named_loopback_databases():
    url = "postgresql://visit_test_owner@127.0.0.1:55439/website_visit_test"

    assert requireSafeWebsiteVisitTestDatabaseUrl(url) == url
