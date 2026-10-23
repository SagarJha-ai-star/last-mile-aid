"""
Slack and PNR (Point of No Return) calculations.

Slack = spare minutes before a delivery becomes unsafe.
PNR  = the last moment a batch can still be rescued by someone else.

All times are in minutes for simplicity.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from engine.geo import travel_time_minutes


def compute_slack(
    expiry_minutes: int,
    elapsed_minutes: float,
    travel_minutes: float,
) -> float:
    """
    Compute the slack (spare minutes) for a delivery.

        slack = expiry_minutes − elapsed_minutes − travel_minutes

    A **negative** slack means the delivery is already unsafe.
    """
    return expiry_minutes - elapsed_minutes - travel_minutes


def compute_pnr(
    batch_created_at: datetime,
    expiry_minutes: int,
    reassignment_overhead_minutes: float,
    travel_minutes_for_backup: float,
) -> datetime:
    """
    Compute the Point of No Return — the latest moment we can still
    hand this batch to a *different* rider and have it arrive safely.

        PNR = created_at + expiry − reassignment_overhead − backup_travel

    After this moment, the current rider is the last hope.
    """
    available = (
        expiry_minutes
        - reassignment_overhead_minutes
        - travel_minutes_for_backup
    )
    return batch_created_at + timedelta(minutes=max(available, 0))


def minutes_since(start: datetime, now: Optional[datetime] = None) -> float:
    """Return minutes elapsed since *start*."""
    now = now or datetime.now(timezone.utc)
    delta = now - start
    return delta.total_seconds() / 60.0


def lease_ttl_seconds(
    expiry_minutes: int,
    elapsed_minutes: float,
    travel_minutes: float,
    buffer_fraction: float = 0.25,
) -> int:
    """
    Derive a lease TTL (in seconds) from the current slack.

    The TTL is the slack minus a safety buffer so there's still time
    for a standby rider to take over if this lease expires.

        ttl = (slack × (1 − buffer_fraction)) × 60

    Returns 0 if the slack is already non-positive.
    """
    slack = compute_slack(expiry_minutes, elapsed_minutes, travel_minutes)
    if slack <= 0:
        return 0
    ttl_minutes = slack * (1 - buffer_fraction)
    return max(int(ttl_minutes * 60), 0)
