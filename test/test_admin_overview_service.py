import datetime
import json
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


def test_constructor_preserves_the_existing_positional_clock_argument():
    service = AdminOverviewService(FakeClient(), lambda: NOW)

    result = service.getUserSignupOverview("7d")

    assert result["lastUpdatedAt"] == "2026-08-26T14:30:00+00:00"


class FakeLangfuseMetrics:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def metrics(self, query):
        self.queries.append(query)
        return SimpleNamespace(data=self.rows)


class FakeLangfuseClient:
    def __init__(self, rows):
        self.metricsEndpoint = FakeLangfuseMetrics(rows)
        self.api = SimpleNamespace(metrics=self.metricsEndpoint)


class FailingLangfuseMetrics:
    def metrics(self, query):
        raise RuntimeError("upstream details must not escape")


def test_token_usage_maps_daily_langfuse_totals_into_signup_chart_buckets():
    langfuse = FakeLangfuseClient([
        {"time_dimension": "2026-08-20T00:00:00Z", "sum_totalTokens": "100"},
        {"time_dimension": "2026-08-24T00:00:00Z", "sum_totalTokens": 250},
        {"time_dimension": "2026-08-26T00:00:00Z", "sum_totalTokens": 400},
    ])
    service = AdminOverviewService(
        langfuseClient=langfuse,
        now=lambda: NOW,
    )

    result = service.getTokenUsageOverview("7d")

    assert result["granularity"] == "day"
    assert result["chart"] == {
        "labels": [
            "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23",
            "2026-08-24", "2026-08-25", "2026-08-26",
        ],
        "datasets": [
            {"label": "LLM tokens", "data": [100, 0, 0, 0, 250, 0, 400]},
        ],
    }
    assert result["totalTokens"] == 750
    assert json.loads(langfuse.metricsEndpoint.queries[0]) == {
        "view": "observations",
        "metrics": [{"measure": "totalTokens", "aggregation": "sum"}],
        "dimensions": [],
        "filters": [],
        "timeDimension": {"granularity": "day"},
        "fromTimestamp": "2026-08-20T00:00:00+00:00",
        "toTimestamp": "2026-08-27T00:00:00+00:00",
        "orderBy": [{"field": "time_dimension", "direction": "asc"}],
        "config": {"row_limit": 1000},
    }


def test_token_usage_rolls_daily_values_into_weekly_buckets():
    langfuse = FakeLangfuseClient([
        {"time_dimension": "2026-08-20T00:00:00Z", "sum_totalTokens": 100},
        {"time_dimension": "2026-08-26T00:00:00Z", "sum_totalTokens": 200},
    ])
    service = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW)

    result = service.getTokenUsageOverview("90d")

    assert result["granularity"] == "week"
    assert len(result["chart"]["labels"]) == 13
    assert result["chart"]["datasets"][0]["data"][-1] == 300


def test_token_usage_rolls_daily_values_into_calendar_months():
    langfuse = FakeLangfuseClient([
        {"time_dimension": "2026-07-31T00:00:00Z", "sum_totalTokens": 100},
        {"time_dimension": "2026-08-02T00:00:00Z", "sum_totalTokens": 200},
    ])
    service = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW)

    result = service.getTokenUsageOverview("1y")

    labels = result["chart"]["labels"]
    data = result["chart"]["datasets"][0]["data"]
    assert data[labels.index("2026-07")] == 100
    assert data[labels.index("2026-08")] == 200


def test_token_usage_reports_unavailable_when_langfuse_is_not_configured():
    service = AdminOverviewService(
        langfuseClient=None,
        now=lambda: NOW,
    )

    with pytest.raises(AdminApiError) as excInfo:
        service.getTokenUsageOverview("7d")

    assert excInfo.value.statusCode == 503
    assert excInfo.value.message == "LLM usage analytics is unavailable"


def test_token_usage_wraps_langfuse_failures_in_the_admin_error_contract():
    langfuse = SimpleNamespace(
        api=SimpleNamespace(metrics=FailingLangfuseMetrics())
    )
    service = AdminOverviewService(
        langfuseClient=langfuse,
        now=lambda: NOW,
    )

    with pytest.raises(AdminApiError) as excInfo:
        service.getTokenUsageOverview("7d")

    assert excInfo.value.statusCode == 500
    assert excInfo.value.message == "Failed to load LLM token usage overview"


def test_token_cost_queries_recorded_cost_and_preserves_subcent_amounts():
    langfuse = FakeLangfuseClient([
        {"time_dimension": "2026-08-20T00:00:00Z", "sum_totalCost": "0.00000125"},
        {"time_dimension": "2026-08-24T00:00:00Z", "sum_totalCost": 0.1},
        {"time_dimension": "2026-08-24T00:00:00Z", "sum_totalCost": "0.2"},
        {"time_dimension": "2026-08-26T00:00:00Z", "sum_totalCost": None},
    ])
    service = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW)

    result = service.getTokenCostOverview("7d")

    assert result == {
        "period": "7d", "granularity": "day", "timezone": "UTC",
        "rangeStart": "2026-08-20T00:00:00+00:00",
        "rangeEnd": "2026-08-27T00:00:00+00:00",
        "lastUpdatedAt": "2026-08-26T14:30:00+00:00",
        "totalCost": 0.30000125, "currency": "USD",
        "chart": {
            "labels": [
                "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23",
                "2026-08-24", "2026-08-25", "2026-08-26",
            ],
            "datasets": [{"label": "LLM cost (USD)", "data": [
                0.00000125, 0, 0, 0, 0.3, 0, 0,
            ]}],
        },
    }
    assert json.loads(langfuse.metricsEndpoint.queries[0]) == {
        "view": "observations",
        "metrics": [{"measure": "totalCost", "aggregation": "sum"}],
        "dimensions": [], "filters": [],
        "timeDimension": {"granularity": "day"},
        "fromTimestamp": "2026-08-20T00:00:00+00:00",
        "toTimestamp": "2026-08-27T00:00:00+00:00",
        "orderBy": [{"field": "time_dimension", "direction": "asc"}],
        "config": {"row_limit": 1000},
    }


@pytest.mark.parametrize("period,granularity,count", [
    ("7d", "day", 7), ("14d", "day", 14), ("30d", "day", 30),
    ("90d", "week", 13), ("6m", "week", 26), ("1y", "month", 12),
])
def test_token_cost_empty_periods_match_token_chart_buckets(period, granularity, count):
    service = AdminOverviewService(langfuseClient=FakeLangfuseClient([]), now=lambda: NOW)

    result = service.getTokenCostOverview(period)
    tokenResult = service.getTokenUsageOverview(period)

    assert result["granularity"] == granularity
    assert result["chart"]["datasets"][0]["data"] == [0] * count
    assert result["totalCost"] == 0
    for field in ("period", "granularity", "timezone", "rangeStart", "rangeEnd", "lastUpdatedAt"):
        assert result[field] == tokenResult[field]
    assert result["chart"]["labels"] == tokenResult["chart"]["labels"]


@pytest.mark.parametrize("period,expectedTail", [
    ("90d", [0, 0.3]), ("6m", [0, 0.3]), ("1y", [0.4, 0.3]),
])
def test_token_cost_rolls_daily_amounts_into_weekly_and_monthly_buckets(period, expectedTail):
    langfuse = FakeLangfuseClient([
        {"time_dimension": "2026-07-31T00:00:00Z", "sum_totalCost": "0.4"},
        {"time_dimension": "2026-08-20T00:00:00Z", "sum_totalCost": "0.1"},
        {"time_dimension": "2026-08-26T00:00:00Z", "sum_totalCost": "0.2"},
    ])
    result = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW).getTokenCostOverview(period)

    assert result["chart"]["datasets"][0]["data"][-2:] == expectedTail
    assert result["totalCost"] == 0.7


def test_token_cost_excludes_outside_range_and_invalid_timestamps():
    langfuse = FakeLangfuseClient([
        {"time_dimension": "2026-08-19T23:59:59Z", "sum_totalCost": 10},
        {"time_dimension": "2026-08-20T00:00:00Z", "sum_totalCost": "0.01"},
        {"time_dimension": "2026-08-27T00:00:00Z", "sum_totalCost": 20},
        {"time_dimension": "invalid", "sum_totalCost": 30},
    ])
    result = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW).getTokenCostOverview("7d")

    assert result["totalCost"] == 0.01
    assert result["chart"]["datasets"][0]["data"] == [0.01, 0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("badCost", ["invalid", "NaN", "Infinity", "-Infinity", -0.1, "1e999"])
def test_token_cost_rejects_invalid_upstream_amounts_instead_of_reporting_zero(badCost):
    langfuse = FakeLangfuseClient([
        {"time_dimension": "2026-08-26T00:00:00Z", "sum_totalCost": badCost},
    ])
    service = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW)

    with pytest.raises(AdminApiError) as excInfo:
        service.getTokenCostOverview("7d")

    assert excInfo.value.statusCode == 500
    assert excInfo.value.message == "Failed to load LLM token cost overview"


def test_token_cost_reports_unconfigured_langfuse():
    service = AdminOverviewService(langfuseClient=None, now=lambda: NOW)
    with pytest.raises(AdminApiError) as excInfo:
        service.getTokenCostOverview("7d")
    assert excInfo.value.statusCode == 503


def test_token_cost_wraps_upstream_failures_without_leaking_details():
    langfuse = SimpleNamespace(api=SimpleNamespace(metrics=FailingLangfuseMetrics()))
    service = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW)
    with pytest.raises(AdminApiError) as excInfo:
        service.getTokenCostOverview("7d")
    assert excInfo.value.statusCode == 500
    assert excInfo.value.message == "Failed to load LLM token cost overview"


def test_token_cost_rejects_invalid_period_before_querying_langfuse():
    service = AdminOverviewService(langfuseClient=None, now=lambda: NOW)
    with pytest.raises(AdminApiError) as excInfo:
        service.getTokenCostOverview("3w")
    assert excInfo.value.statusCode == 422


def test_token_cost_refetches_langfuse_on_each_request():
    langfuse = FakeLangfuseClient([])
    service = AdminOverviewService(langfuseClient=langfuse, now=lambda: NOW)
    first = service.getTokenCostOverview("7d")
    langfuse.metricsEndpoint.rows = [
        {"time_dimension": "2026-08-26T00:00:00Z", "sum_totalCost": "0.125"},
    ]
    second = service.getTokenCostOverview("7d")
    assert first["totalCost"] == 0
    assert second["totalCost"] == 0.125
