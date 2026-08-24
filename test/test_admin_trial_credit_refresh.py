from datetime import datetime, timezone
from unittest.mock import patch

from api.services.credits.creditService import CreditService, _TRIAL_REFRESH_LUA


PERIOD_END = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self, fields=None, fail=False):
        self.fields = dict(fields or {})
        self.fail = fail
        self.calls = []

    def eval(self, script, keyCount, key, *arguments):
        self.calls.append((script, keyCount, key, arguments))
        if self.fail:
            raise RuntimeError("redis unavailable")
        assert script == _TRIAL_REFRESH_LUA
        quota, durableTopup, periodEnd, nextPeriodEnd, generation = map(
            int, arguments
        )
        existingGeneration = int(self.fields.get("tgen", -1))
        if generation < existingGeneration:
            return 0
        existingTopup = self.fields.get("ttop")
        self.fields.update({
            "trem": quota,
            "ttop": durableTopup if existingTopup is None else existingTopup,
            "tquota": quota,
            "pend": periodEnd,
            "pnext": nextPeriodEnd,
            "tgen": generation,
        })
        return 1


def test_trial_refresh_atomically_replaces_monthly_bucket_and_preserves_live_topup():
    redis = FakeRedis({
        "trem": 10,
        "ttop": 125,
        "tquota": 500,
        "pend": 1,
        "pnext": 2,
    })
    service = CreditService()

    with patch.object(service, "_redis", return_value=redis):
        result = service.refreshTrialCreditsCache(
            userId="free-user",
            quota=1_000,
            topupTokens=250,
            periodEnd=PERIOD_END,
            generation=4,
        )

    assert result == "APPLIED"
    assert redis.fields["trem"] == 1_000
    assert redis.fields["tquota"] == 1_000
    assert redis.fields["ttop"] == 125
    assert redis.fields["pend"] == int(PERIOD_END.timestamp())
    assert redis.fields["tgen"] == 4


def test_trial_refresh_seeds_durable_topup_when_cache_is_missing():
    redis = FakeRedis()
    service = CreditService()

    with patch.object(service, "_redis", return_value=redis):
        result = service.refreshTrialCreditsCache(
            userId="free-user",
            quota=1_000,
            topupTokens=250,
            periodEnd=PERIOD_END,
            generation=4,
        )

    assert result == "APPLIED"
    assert redis.fields["ttop"] == 250


def test_trial_refresh_returns_false_when_redis_is_unavailable():
    service = CreditService()

    with patch.object(service, "_redis", return_value=FakeRedis(fail=True)):
        result = service.refreshTrialCreditsCache(
            userId="free-user",
            quota=1_000,
            topupTokens=250,
            periodEnd=PERIOD_END,
            generation=4,
        )

    assert result == "FAILED"


def test_older_trial_refresh_cannot_overwrite_newer_cache_generation():
    redis = FakeRedis({
        "trem": 2_000,
        "ttop": 125,
        "tquota": 2_000,
        "pend": int(PERIOD_END.timestamp()) + 86_400,
        "pnext": int(PERIOD_END.timestamp()) + 2_678_400,
        "tgen": 5,
    })
    before = dict(redis.fields)
    service = CreditService()

    with patch.object(service, "_redis", return_value=redis):
        result = service.refreshTrialCreditsCache(
            userId="free-user",
            quota=1_000,
            topupTokens=250,
            periodEnd=PERIOD_END,
            generation=4,
        )

    assert result == "STALE"
    assert redis.fields == before


def test_credit_redis_pool_has_bounded_connect_and_socket_timeouts():
    from api.services.credits import creditService as module

    service = CreditService()
    with patch.object(module, "_redis_pool", None), patch.object(
        module.redis, "ConnectionPool"
    ) as pool, patch.object(module.redis, "Redis"):
        service._redis()

    options = pool.call_args.kwargs
    assert options["socket_connect_timeout"] > 0
    assert options["socket_timeout"] > 0
