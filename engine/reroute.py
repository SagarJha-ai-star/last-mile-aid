"""
Resilience & Fallback Engine: Mid-journey rerouting and silent-rider escalation.

Implements Sections 5.3, 5.4, and the Page 8 reference algorithms:
- Treat in-transit riders as floating inventory at their live GPS coordinates.
- Dynamic ranked fallback chains (B, C, D) based on remaining slack.
- Instant mid-journey rerouting when a shelter closes or fills.
- Multi-stage silent rider escalation:
    🟡 Yellow (suspect)  – Missed heartbeat (e.g. >2 min)
    🟠 Orange (at risk)  – Past ETA + grace period
    🔴 Red (declared lost) – Slack nearly exhausted; honest write-off and reopen demand
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from engine.models import (
    Batch, Lot, Rider, Shelter,
    LotState, RiderStatus,
)
from engine.geo import travel_time_minutes
from engine.rules import check_safety
from engine.event_log import EventLog


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------

@dataclass
class FallbackOption:
    """A viable alternative destination shelter."""
    shelter_id: str
    shelter_name: str
    available_capacity: int
    slack_minutes: float
    travel_minutes: float


@dataclass
class RerouteResult:
    """Outcome of attempting to reroute a rider mid-transit."""
    success: bool
    rider_id: str
    lot_id: str
    old_shelter_id: Optional[str]
    new_shelter_id: Optional[str]
    slack_minutes: float
    reason: str
    fallback_chain: list[FallbackOption] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Functional / Standalone Algorithms
# ---------------------------------------------------------------------------

def compute_fallback_chain(
    rider_lat: float,
    rider_lon: float,
    cargo_batch: Batch,
    units: int,
    shelters: list[Shelter],
    now_minutes: float = 0.0,
    exclude_shelter_id: Optional[str] = None,
    traffic_multiplier: float = 1.0,
) -> list[FallbackOption]:
    """
    Produce a ranked list of viable destination shelters for an in-transit rider:
    - Must be open.
    - Must satisfy safety/compatibility rules for cargo_batch.
    - Must have sufficient available capacity: (demand - inbound) >= units.
    - Must have non-negative slack from rider's LIVE position.
    Ranked by remaining slack descending (safest first).
    """
    chain: list[FallbackOption] = []
    effective_expiry = cargo_batch.expiry_minutes - cargo_batch.serve_buffer_minutes

    for s in shelters:
        if s.id == exclude_shelter_id:
            continue
        if not s.is_open:
            continue

        avail_cap = max(s.demand - s.inbound, 0)
        if avail_cap < units:
            continue

        safety = check_safety(cargo_batch.tags, s.capabilities)
        if not safety.allowed:
            continue

        eta = travel_time_minutes(
            rider_lat, rider_lon,
            s.lat, s.lon,
            traffic_multiplier=traffic_multiplier,
        )
        elapsed_min = max(now_minutes - getattr(cargo_batch, "created_at_minutes", 0.0), 0.0)
        slack = effective_expiry - elapsed_min - eta
        if slack >= 0:
            chain.append(FallbackOption(
                shelter_id=s.id,
                shelter_name=s.name,
                available_capacity=avail_cap,
                slack_minutes=round(slack, 2),
                travel_minutes=round(eta, 2),
            ))

    # Sort by greatest slack (safest destination first)
    chain.sort(key=lambda opt: opt.slack_minutes, reverse=True)
    return chain


def reroute_in_transit_rider(
    rider: Rider,
    lot: Lot,
    cargo_batch: Batch,
    shelters: list[Shelter],
    now_minutes: float = 0.0,
    traffic_multiplier: float = 1.0,
) -> RerouteResult:
    """
    Mid-journey re-planning from the rider's LIVE coordinates (Page 8 C++ algorithm):
    Finds the best alternative shelter with the highest positive slack.
    """
    chain = compute_fallback_chain(
        rider.lat, rider.lon,
        cargo_batch,
        lot.quantity,
        shelters,
        now_minutes=now_minutes,
        exclude_shelter_id=lot.target_shelter_id,
        traffic_multiplier=traffic_multiplier,
    )

    if not chain:
        return RerouteResult(
            success=False,
            rider_id=rider.id,
            lot_id=lot.id,
            old_shelter_id=lot.target_shelter_id,
            new_shelter_id=None,
            slack_minutes=-1.0,
            reason="No feasible alternative shelter with positive slack and capacity found",
            fallback_chain=[],
        )

    best = chain[0]
    return RerouteResult(
        success=True,
        rider_id=rider.id,
        lot_id=lot.id,
        old_shelter_id=lot.target_shelter_id,
        new_shelter_id=best.shelter_id,
        slack_minutes=best.slack_minutes,
        reason=(
            f"Instant reroute to {best.shelter_name}: {best.slack_minutes:.1f}m slack remaining "
            f"({best.travel_minutes:.1f}m ETA from live position)"
        ),
        fallback_chain=chain,
    )


# ---------------------------------------------------------------------------
# Silent Rider Stage Evaluator
# ---------------------------------------------------------------------------

def evaluate_silent_rider_health(
    minutes_since_ping: float,
    now_minutes: float,
    expected_arrival_minutes: float,
    slack_minutes: float,
    yellow_threshold_minutes: float = 2.0,
    orange_grace_minutes: float = 3.0,
    red_slack_threshold_minutes: float = 5.0,
) -> RiderStatus:
    """
    Classify in-transit rider status into Stages per Section 5.4:
    - OK: Normal heartbeat received recently.
    - 🟡 Yellow (suspect): No heartbeat for ~2 minutes.
    - 🟠 Orange (at risk): Past expected arrival + grace period with time still left.
    - 🔴 Red (declared lost): Slack nearly exhausted (< 5 min) with no contact.
    """
    # Red: Slack nearly gone with no contact -> lost
    if slack_minutes <= red_slack_threshold_minutes and minutes_since_ping >= yellow_threshold_minutes:
        return RiderStatus.SILENT_RED

    # Orange: Past expected arrival + grace, but still has some slack
    if now_minutes > (expected_arrival_minutes + orange_grace_minutes) and minutes_since_ping >= yellow_threshold_minutes:
        return RiderStatus.SILENT_ORANGE

    # Yellow: Missed ping / no heartbeat
    if minutes_since_ping >= yellow_threshold_minutes:
        return RiderStatus.SILENT_YELLOW

    return RiderStatus.BUSY


# ---------------------------------------------------------------------------
# Resilience Coordinator Class
# ---------------------------------------------------------------------------

class ResilienceManager:
    """
    Coordinates rerouting and silent rider escalation with the EventLog.
    """

    def __init__(self, event_log: Optional[EventLog] = None):
        self.event_log = event_log or EventLog()

    def handle_shelter_closed_or_full(
        self,
        closed_shelter_id: str,
        lots: dict[str, Lot],
        riders: dict[str, Rider],
        batches: dict[str, Batch],
        shelters: dict[str, Shelter],
        now_minutes: float = 0.0,
        traffic_multiplier: float = 1.0,
    ) -> list[RerouteResult]:
        """
        When a shelter closes or fills ('pulled rug'), reroute all affected
        in-transit or assigned riders targeting that shelter, processed in order
        of least slack first.
        """
        shelter = shelters.get(closed_shelter_id)
        if shelter:
            shelter.is_open = False

        self.event_log.append(
            "shelter_changed",
            {"shelter_id": closed_shelter_id, "is_open": False},
            reason=f"Shelter {shelter.name if shelter else closed_shelter_id} closed / capacity filled",
        )

        affected_lots = [
            lot for lot in lots.values()
            if lot.target_shelter_id == closed_shelter_id
            and lot.state in (LotState.ASSIGNED, LotState.PICKED_UP)
        ]

        results: list[RerouteResult] = []

        for lot in affected_lots:
            rider = riders.get(lot.assigned_rider_id) if lot.assigned_rider_id else None
            batch = batches.get(lot.batch_id)
            if not rider or not batch:
                continue

            res = reroute_in_transit_rider(
                rider, lot, batch,
                list(shelters.values()),
                now_minutes=now_minutes,
                traffic_multiplier=traffic_multiplier,
            )
            results.append(res)

            if res.success and res.new_shelter_id:
                old_shelter_id = lot.target_shelter_id
                lot.target_shelter_id = res.new_shelter_id
                lot.version += 1

                # Update inbound counters
                if shelter:
                    shelter.inbound = max(shelter.inbound - lot.quantity, 0)
                new_shelter = shelters.get(res.new_shelter_id)
                if new_shelter:
                    new_shelter.inbound += lot.quantity

                self.event_log.append(
                    "rerouted",
                    {
                        "lot_id": lot.id,
                        "rider_id": rider.id,
                        "from_shelter": old_shelter_id,
                        "to_shelter": res.new_shelter_id,
                        "slack_minutes": res.slack_minutes,
                    },
                    reason=f"Instant reroute for Rider {rider.name}: {res.reason}",
                )
            else:
                # No feasible alternative -> honest escalation
                self.event_log.append(
                    "reroute_failed",
                    {"lot_id": lot.id, "rider_id": rider.id},
                    reason=f"Safe discard escalation: Rider {rider.name} has no feasible alternative destination",
                )

        return results

    def escalate_silent_rider(
        self,
        rider: Rider,
        lot: Lot,
        shelter: Shelter,
        stage: RiderStatus,
        now_minutes: float = 0.0,
    ) -> None:
        """
        Escalate a silent rider who has cargo in hand through Yellow, Orange, and Red.
        At Red:
        - Only that single chunk is declared lost (limiting damage).
        - Lot is marked LOST.
        - Shelter demand is immediately reopened.
        - Loss is recorded honestly in the event log.
        - Rider reliability is reduced.
        """
        rider.status = stage

        if stage == RiderStatus.SILENT_YELLOW:
            self.event_log.append(
                "rider_alert",
                {"rider_id": rider.id, "lot_id": lot.id, "stage": "yellow"},
                reason=f"🟡 Yellow alert: Rider {rider.name} missed heartbeat (>2m). Contacting rider and shelter.",
            )

        elif stage == RiderStatus.SILENT_ORANGE:
            self.event_log.append(
                "rider_alert",
                {"rider_id": rider.id, "lot_id": lot.id, "stage": "orange"},
                reason=f"🟠 Orange alert: Rider {rider.name} past expected arrival with slack remaining. Alerting backup courier.",
            )

        elif stage == RiderStatus.SILENT_RED:
            # Declare single chunk lost, honest write-off
            lot.state = LotState.LOST
            lot.version += 1

            # Reopen shelter demand immediately
            shelter.inbound = max(shelter.inbound - lot.quantity, 0)

            # Penalize rider reliability
            rider.reliability = max(round(rider.reliability - 0.3, 2), 0.1)

            self.event_log.append(
                "lot_lost",
                {
                    "lot_id": lot.id,
                    "rider_id": rider.id,
                    "shelter_id": shelter.id,
                    "units_lost": lot.quantity,
                },
                reason=(
                    f"🔴 Red alert: Rider {rider.name} declared lost after pickup. "
                    f"Only chunk {lot.id} ({lot.quantity} meals) written off. "
                    f"Shelter {shelter.name} demand reopened immediately."
                ),
            )
