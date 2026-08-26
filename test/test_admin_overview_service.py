import datetime
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.adminErrors import AdminApiError
from api.services.adminOverviewService import (
    ADMIN_OVERVIEW_BATCH_SIZE,
    AdminOverviewService,
)


NOW = datetime.datetime(2026, 8, 26, 14, 30, 0, tzinfo=datetime.timezone.utc)


class FakeQuery:
    """Minimal stand-in for the Supabase query builder used by the service."""

    def __init__(self, client, tableName):
        self.client = client
        self.tableName = tableName
        self.selectFields = None
        self.filters = []
        self.orderColumn = None
        self.rangeBounds = None

    def select(self, fields):
        self.selectFields = fields
        self.client.selects.append(fields)
        return self

    def gte(self, field, value):
        self.filters.append(("gte", field, value))
        self.client.filters.append(("gte", field, value))
        return self

    def lt(self, field, value):
        self.filters.append(("lt", field, value))
        self.client.filters.append(("lt", field, value))
        return self

    def order(self, column):
        self.orderColumn = column
        return self

    def range(self, start, end):
        self.rangeBounds = (start, end)
        self.client.ranges.append((start, end))
        return self

    def execute(self):
        if self.client.failure is not None:
            raise self.client.failure

        matching = [row for row in self.client.rows if self._matches(row)]
        if self.orderColumn is not None:
            matching.sort(key=lambda row: str(row.get(self.orderColumn, "")))
        if self.rangeBounds is not None:
            start, end = self.rangeBounds
            matching = matching[start:end + 1]
        return SimpleNamespace(data=[self._project(row) for row in matching])

    def _matches(self, row):
        for operation, field, value in self.filters:
            actual = row.get(field)
            if actual is None:
                return False
            if operation == "gte" and str(actual) < str(value):
                return False
            if operation == "lt" and str(actual) >= str(value):
                return False
        return True

    def _project(self, row):
        if self.selectFields is None:
            return dict(row)
        fields = self.selectFields.split(",")
        return {field: row.get(field) for field in fields}


class FakeClient:
    def __init__(self, createdAtValues=(), failure=None):
        self.rows = [{"createdAt": value} for value in createdAtValues]
        self.selects = []
        self.filters = []
        self.ranges = []
        self.failure = failure

    def table(self, tableName):
        if tableName != "Users":
            raise AssertionError(f"Unexpected table: {tableName}")
        return FakeQuery(self, tableName)


def buildService(createdAtValues=(), failure=None, now=NOW):
    client = FakeClient(createdAtValues, failure)
    service = AdminOverviewService(client=client, now=lambda: now)
    return service, client


def test_thirty_day_period_returns_one_daily_bucket_per_day():
    service, _client = buildService()

    result = service.getUserSignupOverview("30d")

    assert result["granularity"] == "day"
    assert len(result["chart"]["labels"]) == 30


def test_daily_labels_end_on_the_current_utc_day():
    service, _client = buildService()

    result = service.getUserSignupOverview("7d")

    assert result["chart"]["labels"][-1] == "2026-08-26"
    assert result["chart"]["labels"][0] == "2026-08-20"


def test_ninety_day_period_buckets_by_week():
    service, _client = buildService()

    result = service.getUserSignupOverview("90d")

    assert result["granularity"] == "week"
    assert len(result["chart"]["labels"]) == 13


def test_six_month_period_buckets_into_twenty_six_weeks():
    service, _client = buildService()

    result = service.getUserSignupOverview("6m")

    assert result["granularity"] == "week"
    assert len(result["chart"]["labels"]) == 26


def test_one_year_period_buckets_into_twelve_calendar_months():
    service, _client = buildService()

    result = service.getUserSignupOverview("1y")

    assert result["granularity"] == "month"
    assert result["chart"]["labels"] == [
        "2025-09", "2025-10", "2025-11", "2025-12",
        "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08",
    ]


def test_unknown_period_is_rejected():
    service, _client = buildService()

    with pytest.raises(AdminApiError) as excInfo:
        service.getUserSignupOverview("3w")

    assert excInfo.value.statusCode == 422


def test_signups_are_counted_into_their_utc_day_bucket():
    service, _client = buildService([
        "2026-08-26T09:00:00+00:00",
        "2026-08-26T23:59:59+00:00",
        "2026-08-24T00:00:00+00:00",
    ])

    result = service.getUserSignupOverview("7d")

    labels = result["chart"]["labels"]
    data = result["chart"]["datasets"][0]["data"]
    assert data[labels.index("2026-08-26")] == 2
    assert data[labels.index("2026-08-24")] == 1


def test_days_without_signups_are_zero_filled():
    service, _client = buildService(["2026-08-26T09:00:00+00:00"])

    result = service.getUserSignupOverview("7d")

    data = result["chart"]["datasets"][0]["data"]
    assert data == [0, 0, 0, 0, 0, 0, 1]


def test_total_signups_sums_every_bucket():
    service, _client = buildService([
        "2026-08-26T09:00:00+00:00",
        "2026-08-25T09:00:00+00:00",
        "2026-08-21T09:00:00+00:00",
    ])

    result = service.getUserSignupOverview("7d")

    assert result["totalSignups"] == 3


def test_signups_older_than_the_window_are_excluded():
    service, _client = buildService([
        "2026-08-19T23:59:59+00:00",
        "2026-08-20T00:00:00+00:00",
    ])

    result = service.getUserSignupOverview("7d")

    assert result["totalSignups"] == 1


def test_query_is_bounded_by_the_requested_range():
    service, client = buildService()

    result = service.getUserSignupOverview("7d")

    assert ("gte", "createdAt", result["rangeStart"]) in client.filters
    assert ("lt", "createdAt", result["rangeEnd"]) in client.filters


def test_only_the_signup_timestamp_column_is_selected():
    service, client = buildService()

    service.getUserSignupOverview("7d")

    assert client.selects == ["createdAt"]


def test_signups_beyond_one_batch_are_paginated():
    service, client = buildService(
        ["2026-08-26T09:00:00+00:00"] * (ADMIN_OVERVIEW_BATCH_SIZE + 25)
    )

    result = service.getUserSignupOverview("7d")

    assert result["totalSignups"] == ADMIN_OVERVIEW_BATCH_SIZE + 25
    assert len(client.ranges) == 2


def test_weekly_buckets_count_signups_within_their_seven_day_span():
    service, _client = buildService([
        "2026-08-26T09:00:00+00:00",
        "2026-08-20T09:00:00+00:00",
    ])

    result = service.getUserSignupOverview("90d")

    data = result["chart"]["datasets"][0]["data"]
    assert data[-1] == 2
    assert sum(data) == 2


def test_monthly_buckets_count_signups_within_their_calendar_month():
    service, _client = buildService([
        "2026-08-02T09:00:00+00:00",
        "2026-07-31T23:00:00+00:00",
    ])

    result = service.getUserSignupOverview("1y")

    labels = result["chart"]["labels"]
    data = result["chart"]["datasets"][0]["data"]
    assert data[labels.index("2026-08")] == 1
    assert data[labels.index("2026-07")] == 1


def test_zulu_suffix_timestamps_are_parsed_as_utc():
    service, _client = buildService(["2026-08-26T09:00:00Z"])

    result = service.getUserSignupOverview("7d")

    assert result["chart"]["datasets"][0]["data"][-1] == 1


def test_naive_timestamps_are_treated_as_utc():
    service, _client = buildService(["2026-08-26 09:00:00"])

    result = service.getUserSignupOverview("7d")

    assert result["chart"]["datasets"][0]["data"][-1] == 1


def test_unparsable_timestamps_are_skipped_rather_than_failing_the_request():
    service, _client = buildService([
        "not-a-timestamp",
        "2026-08-26T09:00:00+00:00",
    ])

    result = service.getUserSignupOverview("7d")

    assert result["totalSignups"] == 1


def test_backend_failure_surfaces_as_a_server_error():
    service, _client = buildService(failure=RuntimeError("supabase unreachable"))

    with pytest.raises(AdminApiError) as excInfo:
        service.getUserSignupOverview("7d")

    assert excInfo.value.statusCode == 500
