"""Admin overview aggregates for signup and LLM token-usage line charts."""

__all__ = [
    "ADMIN_OVERVIEW_BATCH_SIZE",
    "ADMIN_OVERVIEW_PERIODS",
    "AdminOverviewService",
    "getAdminOverviewService",
]


import bisect
import datetime
import json

from loguru import logger

from api.adminErrors import AdminApiError


ADMIN_OVERVIEW_BATCH_SIZE = 1000
SIGNUP_COLUMN = "createdAt"
SIGNUP_SERIES_LABEL = "New users"
TOKEN_USAGE_SERIES_LABEL = "LLM tokens"
_UNSET = object()

# period -> (granularity, bucket count). Weekly periods are rounded to whole
# weeks so every bucket spans the same number of days.
ADMIN_OVERVIEW_PERIOD_SPECS = {
    "7d": ("day", 7),
    "14d": ("day", 14),
    "30d": ("day", 30),
    "90d": ("week", 13),
    "6m": ("week", 26),
    "1y": ("month", 12),
}
ADMIN_OVERVIEW_PERIODS = tuple(ADMIN_OVERVIEW_PERIOD_SPECS)
DEFAULT_ADMIN_OVERVIEW_PERIOD = "30d"


def _utcNow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parseTimestamp(rawValue) -> datetime.datetime | None:
    """Parse a stored signup timestamp, normalising it to UTC."""
    if isinstance(rawValue, datetime.datetime):
        parsed = rawValue
    elif isinstance(rawValue, str):
        text = rawValue.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _startOfMonth(moment: datetime.datetime) -> datetime.datetime:
    return moment.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _subtractMonths(moment: datetime.datetime, months: int) -> datetime.datetime:
    monthIndex = moment.year * 12 + (moment.month - 1) - months
    return moment.replace(
        year=monthIndex // 12, month=monthIndex % 12 + 1
    )


class AdminOverviewService:
    def __init__(self, client=None, now=None, langfuseClient=_UNSET):
        self._client = client
        self._langfuseClient = langfuseClient
        self._now = now or _utcNow

    @property
    def client(self):
        if self._client is None:
            from api.commons import client
            self._client = client
        return self._client

    @property
    def langfuseClient(self):
        if self._langfuseClient is _UNSET:
            from utils.langfuseClient import langfuseClient
            self._langfuseClient = langfuseClient
        return self._langfuseClient

    def getUserSignupOverview(self, period: str) -> dict:
        granularity, bucketCount = self._resolvePeriod(period)
        now = self._now()
        boundaries = self._bucketBoundaries(now, granularity, bucketCount)
        labels = [
            self._formatLabel(start, granularity) for start, _end in boundaries
        ]
        rangeStart = boundaries[0][0]
        rangeEnd = boundaries[-1][1]
        counts = self._countSignups(boundaries, rangeStart, rangeEnd)

        return {
            "period": period,
            "granularity": granularity,
            "timezone": "UTC",
            "rangeStart": rangeStart.isoformat(),
            "rangeEnd": rangeEnd.isoformat(),
            "lastUpdatedAt": now.isoformat(),
            "totalSignups": sum(counts),
            "chart": {
                "labels": labels,
                "datasets": [
                    {"label": SIGNUP_SERIES_LABEL, "data": counts},
                ],
            },
        }

    def getTokenUsageOverview(self, period: str) -> dict:
        granularity, bucketCount = self._resolvePeriod(period)
        now = self._now()
        boundaries = self._bucketBoundaries(now, granularity, bucketCount)
        labels = [
            self._formatLabel(start, granularity) for start, _end in boundaries
        ]
        rangeStart = boundaries[0][0]
        rangeEnd = boundaries[-1][1]
        counts = [0] * len(boundaries)
        starts = [start for start, _end in boundaries]

        for row in self._fetchTokenUsageRows(rangeStart, rangeEnd):
            moment = _parseTimestamp(row.get("time_dimension"))
            if moment is None or moment < rangeStart or moment >= rangeEnd:
                continue
            try:
                tokens = max(0, int(row.get("sum_totalTokens") or 0))
            except (TypeError, ValueError):
                tokens = 0
            counts[bisect.bisect_right(starts, moment) - 1] += tokens

        return {
            "period": period,
            "granularity": granularity,
            "timezone": "UTC",
            "rangeStart": rangeStart.isoformat(),
            "rangeEnd": rangeEnd.isoformat(),
            "lastUpdatedAt": now.isoformat(),
            "totalTokens": sum(counts),
            "chart": {
                "labels": labels,
                "datasets": [
                    {"label": TOKEN_USAGE_SERIES_LABEL, "data": counts},
                ],
            },
        }

    def _fetchTokenUsageRows(
        self, rangeStart: datetime.datetime, rangeEnd: datetime.datetime
    ) -> list[dict]:
        if self.langfuseClient is None:
            raise AdminApiError(503, "LLM usage analytics is unavailable")

        query = json.dumps({
            "view": "observations",
            "metrics": [{"measure": "totalTokens", "aggregation": "sum"}],
            "dimensions": [],
            "filters": [],
            "timeDimension": {"granularity": "day"},
            "fromTimestamp": rangeStart.isoformat(),
            "toTimestamp": rangeEnd.isoformat(),
            "orderBy": [{"field": "time_dimension", "direction": "asc"}],
            "config": {"row_limit": 1000},
        })
        try:
            response = self.langfuseClient.api.metrics.metrics(query=query)
            rows = getattr(response, "data", None) or []
            return [
                row if isinstance(row, dict) else vars(row)
                for row in rows
            ]
        except Exception as exc:
            logger.error("Admin token usage overview failed: {}", type(exc).__name__)
            raise AdminApiError(
                500, "Failed to load LLM token usage overview"
            ) from exc

    def _countSignups(
        self,
        boundaries: list[tuple[datetime.datetime, datetime.datetime]],
        rangeStart: datetime.datetime,
        rangeEnd: datetime.datetime,
    ) -> list[int]:
        counts = [0] * len(boundaries)
        starts = [start for start, _end in boundaries]
        for row in self._fetchSignupRows(rangeStart, rangeEnd):
            moment = _parseTimestamp(row.get(SIGNUP_COLUMN))
            if moment is None or moment < rangeStart or moment >= rangeEnd:
                continue
            counts[bisect.bisect_right(starts, moment) - 1] += 1
        return counts

    def _fetchSignupRows(
        self, rangeStart: datetime.datetime, rangeEnd: datetime.datetime
    ) -> list[dict]:
        rows = []
        start = 0
        try:
            while True:
                batch = (
                    self.client.table("Users")
                    .select(SIGNUP_COLUMN)
                    .gte(SIGNUP_COLUMN, rangeStart.isoformat())
                    .lt(SIGNUP_COLUMN, rangeEnd.isoformat())
                    .order(SIGNUP_COLUMN)
                    .range(start, start + ADMIN_OVERVIEW_BATCH_SIZE - 1)
                    .execute().data
                ) or []
                rows.extend(batch)
                if len(batch) < ADMIN_OVERVIEW_BATCH_SIZE:
                    return rows
                start += ADMIN_OVERVIEW_BATCH_SIZE
        except Exception as exc:
            logger.error("Admin signup overview failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to load user signup overview") from exc

    def _resolvePeriod(self, period: str) -> tuple[str, int]:
        spec = ADMIN_OVERVIEW_PERIOD_SPECS.get(period)
        if spec is None:
            raise AdminApiError(
                422,
                "Invalid overview period",
                {"period": f"Must be one of {', '.join(ADMIN_OVERVIEW_PERIODS)}"},
            )
        return spec

    def _bucketBoundaries(
        self, now: datetime.datetime, granularity: str, bucketCount: int
    ) -> list[tuple[datetime.datetime, datetime.datetime]]:
        if granularity == "month":
            firstOfThisMonth = _startOfMonth(now)
            starts = [
                _subtractMonths(firstOfThisMonth, bucketCount - 1 - index)
                for index in range(bucketCount)
            ]
            ends = starts[1:] + [_subtractMonths(firstOfThisMonth, -1)]
            return list(zip(starts, ends))

        spanDays = 1 if granularity == "day" else 7
        endOfToday = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + datetime.timedelta(days=1)
        starts = [
            endOfToday
            - datetime.timedelta(days=spanDays * (bucketCount - index))
            for index in range(bucketCount)
        ]
        return [
            (start, start + datetime.timedelta(days=spanDays))
            for start in starts
        ]

    def _formatLabel(self, start: datetime.datetime, granularity: str) -> str:
        if granularity == "month":
            return start.strftime("%Y-%m")
        return start.strftime("%Y-%m-%d")


_adminOverviewService: AdminOverviewService | None = None


def getAdminOverviewService() -> AdminOverviewService:
    global _adminOverviewService
    if _adminOverviewService is None:
        _adminOverviewService = AdminOverviewService()
    return _adminOverviewService
