"""Unit tests for engine/slack.py — slack, PNR, and lease TTL."""

from datetime import datetime, timedelta, timezone
from engine.slack import (
    compute_slack,
    compute_pnr,
    minutes_since,
    lease_ttl_seconds,
)


class TestComputeSlack:
    def test_plenty_of_slack(self):
        # 60 min expiry, 10 elapsed, 20 travel → 30 min slack
        assert compute_slack(60, 10, 20) == 30.0

    def test_zero_slack(self):
        assert compute_slack(60, 30, 30) == 0.0

    def test_negative_slack_means_unsafe(self):
        slack = compute_slack(60, 40, 30)
        assert slack < 0

    def test_no_time_elapsed(self):
        assert compute_slack(75, 0, 20) == 55.0


class TestComputePNR:
    def test_basic_pnr(self):
        created = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        # 60 min expiry, 5 min overhead, 15 min backup travel
        # PNR = created + (60 - 5 - 15) = created + 40 min
        pnr = compute_pnr(created, 60, 5, 15)
        expected = created + timedelta(minutes=40)
        assert pnr == expected

    def test_pnr_clamps_to_creation_when_impossible(self):
        created = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        # overhead + backup > expiry → PNR = created (can't be rescued)
        pnr = compute_pnr(created, 10, 20, 30)
        assert pnr == created

    def test_pnr_is_before_expiry(self):
        created = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        pnr = compute_pnr(created, 75, 5, 20)
        expiry = created + timedelta(minutes=75)
        assert pnr < expiry


class TestMinutesSince:
    def test_ten_minutes(self):
        now = datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc)
        start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert minutes_since(start, now) == 10.0

    def test_zero(self):
        t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert minutes_since(t, t) == 0.0


class TestLeaseTTL:
    def test_positive_slack(self):
        # slack = 60 - 10 - 20 = 30 min; buffer 25% → 22.5 min → 1350 sec
        ttl = lease_ttl_seconds(60, 10, 20, buffer_fraction=0.25)
        assert ttl == 1350

    def test_zero_slack_returns_zero(self):
        ttl = lease_ttl_seconds(60, 30, 30)
        assert ttl == 0

    def test_negative_slack_returns_zero(self):
        ttl = lease_ttl_seconds(60, 40, 30)
        assert ttl == 0

    def test_no_buffer(self):
        # slack = 30 min, buffer = 0 → 30 * 60 = 1800 sec
        ttl = lease_ttl_seconds(60, 10, 20, buffer_fraction=0.0)
        assert ttl == 1800
