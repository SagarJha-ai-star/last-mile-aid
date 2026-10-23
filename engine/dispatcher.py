"""
Dispatcher: chunking, assignment, and adaptive lease management.

Responsibilities:
- Calculate rescue plans with rescuable counts, needed riders, and PNR countdown.
- Split a Batch into rider-sized Lots.
- Match Lots to Riders and Shelters using greedy score optimization.
- Concurrency-safe lease claims (versioned compare-and-set).
- Lease expiration handling with automatic standby rider takeover.
- Early penalty-free release.
- Surge-broadcast notification when short on capacity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from engine.models import (
    Batch, Lot, Rider, Shelter, Lease,
    LotState, RiderStatus, LeaseState,
)
from engine.geo import travel_time_minutes
from engine.rules import check_safety
from engine.slack import (
    compute_slack,
    compute_pnr,
    adaptive_lease_ttl_minutes,
    adaptive_lease_ttl_seconds,
)
from engine.event_log import EventLog


# ---------------------------------------------------------------------------
# Data Transfer / Plan Objects
# ---------------------------------------------------------------------------

@dataclass
class ChunkPlan:
    """A planned single-rider chunk of a batch."""
    rider_id: str
    rider_name: str
    shelter_id: str
    shelter_name: str
    units: int
    slack_minutes: float
    lease_ttl_seconds: int
    pnr_minutes: float
    score: float


@dataclass
class RescuePlan:
    """The complete rescue plan for a donated batch."""
    batch_id: str
    item: str
    total_quantity: int
    rescuable_units: int
    unrescued_units: int
    needed_riders: int
    chunks: list[ChunkPlan] = field(default_factory=list)
    pnr_minutes: float = 0.0
    surge_broadcast: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# Core Algorithm Functions (Functional / Standalone)
# ---------------------------------------------------------------------------

def calculate_chunk_slack(
    batch: Batch,
    rider: Rider,
    shelter: Shelter,
    now_minutes: float = 0.0,
    traffic_multiplier: float = 1.0,
    loading_minutes: float = 3.0,
) -> float:
    """
    Spare minutes before this delivery leg becomes unsafe:
        pickup_time = max(now, rider.free_at) + eta(rider, batch)
        drop_time   = pickup_time + loading + eta(batch, shelter)
        slack       = (batch.expiry - batch.serve_buffer) - drop_time
    Negative slack means delivery is physically infeasible before expiry.
    """
    eta_to_batch = travel_time_minutes(
        rider.lat, rider.lon,
        batch.donor_lat, batch.donor_lon,
        traffic_multiplier=traffic_multiplier,
    )
    rider_wait = max(rider.free_at_minutes - now_minutes, 0.0)
    eta_to_shelter = travel_time_minutes(
        batch.donor_lat, batch.donor_lon,
        shelter.lat, shelter.lon,
        traffic_multiplier=traffic_multiplier,
    )
    trip_duration = rider_wait + eta_to_batch + loading_minutes + eta_to_shelter

    batch_created = getattr(batch, "created_at_minutes", 0.0)
    elapsed_minutes = max(now_minutes - batch_created, 0.0)
    effective_expiry = batch.expiry_minutes - batch.serve_buffer_minutes
    return effective_expiry - elapsed_minutes - trip_duration


def calculate_rescue_plan(
    batch: Batch,
    riders: list[Rider],
    shelters: list[Shelter],
    now_minutes: float = 0.0,
    traffic_multiplier: float = 1.0,
) -> RescuePlan:
    """
    Produce the optimal greedy rescue plan for a Batch:
    1. Filter out closed shelters or safety rule violations.
    2. Repeatedly pick the highest-scoring (rider, shelter, units) chunk:
         score = units * reliability * min(1.0, 0.7 + 0.03 * slack)
    3. Determine rescuable count, unrescued count, and surge broadcast need.
    """
    units_remaining = batch.units_left if batch.units_left > 0 else batch.quantity
    shelter_capacities = {
        s.id: max(s.demand - s.inbound, 0) for s in shelters if s.is_open
    }
    available_riders = {
        r.id: r for r in riders if r.status == RiderStatus.AVAILABLE and r.capacity > 0
    }

    chunks: list[ChunkPlan] = []
    used_riders: set[str] = set()

    while units_remaining > 0 and len(used_riders) < len(available_riders):
        best_candidate: Optional[tuple[Rider, Shelter, int, float, float]] = None
        best_score = -1.0

        for r_id, rider in available_riders.items():
            if r_id in used_riders:
                continue

            for shelter in shelters:
                if not shelter.is_open:
                    continue
                rem_shelter_cap = shelter_capacities.get(shelter.id, 0)
                if rem_shelter_cap <= 0:
                    continue

                # Safety check
                safety = check_safety(batch.tags, shelter.capabilities)
                if not safety.allowed:
                    continue

                # Slack check
                sl = calculate_chunk_slack(
                    batch, rider, shelter, now_minutes, traffic_multiplier
                )
                if sl < 0:
                    continue

                alloc_units = min(rider.capacity, units_remaining, rem_shelter_cap)
                if alloc_units <= 0:
                    continue

                # Reference C++ scoring formula
                confidence = rider.reliability * min(1.0, 0.7 + 0.03 * sl)
                score = alloc_units * confidence

                if score > best_score:
                    best_score = score
                    best_candidate = (rider, shelter, alloc_units, sl, score)

        if not best_candidate:
            break

        rider, shelter, units, sl, score = best_candidate
        used_riders.add(rider.id)
        shelter_capacities[shelter.id] -= units
        units_remaining -= units

        ttl_sec = adaptive_lease_ttl_seconds(sl)
        eta_to_shelter = travel_time_minutes(
            batch.donor_lat, batch.donor_lon,
            shelter.lat, shelter.lon,
            traffic_multiplier=traffic_multiplier,
        )
        batch_created = getattr(batch, "created_at_minutes", 0.0)
        elapsed_min = max(now_minutes - batch_created, 0.0)
        pnr_min = max(batch.expiry_minutes - elapsed_min - 5.0 - eta_to_shelter, 0.0)

        chunks.append(ChunkPlan(
            rider_id=rider.id,
            rider_name=rider.name,
            shelter_id=shelter.id,
            shelter_name=shelter.name,
            units=units,
            slack_minutes=round(sl, 2),
            lease_ttl_seconds=ttl_sec,
            pnr_minutes=round(pnr_min, 2),
            score=round(score, 2),
        ))

    rescuable_units = sum(c.units for c in chunks)
    unrescued_units = batch.quantity - rescuable_units
    needed_riders = len(chunks)

    # Surge broadcast if not all units could be rescued or extra riders needed
    is_short = (rescuable_units < batch.quantity)
    surge_broadcast = is_short or (needed_riders > len(available_riders))

    min_pnr = min((c.pnr_minutes for c in chunks), default=0.0)
    reason = (
        f"Rescuable: {rescuable_units}/{batch.quantity} across {needed_riders} riders. "
        + ("Surge broadcast triggered (shortage)." if surge_broadcast else "Fully covered.")
    )

    return RescuePlan(
        batch_id=batch.id,
        item=batch.item,
        total_quantity=batch.quantity,
        rescuable_units=rescuable_units,
        unrescued_units=unrescued_units,
        needed_riders=needed_riders,
        chunks=chunks,
        pnr_minutes=min_pnr,
        surge_broadcast=surge_broadcast,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Dispatcher Engine Class
# ---------------------------------------------------------------------------

class Dispatcher:
    """
    Core Dispatch Engine managing batches, lots, leases, and state transitions.
    """

    def __init__(self, event_log: Optional[EventLog] = None):
        self.event_log = event_log or EventLog()
        self.batches: dict[str, Batch] = {}
        self.lots: dict[str, Lot] = {}
        self.leases: dict[str, Lease] = {}
        self.riders: dict[str, Rider] = {}
        self.shelters: dict[str, Shelter] = {}
        self.standbys: dict[str, str] = {}  # lot_id -> standby rider_id

    # -----------------------------------------------------------------------
    # Entity Registration
    # -----------------------------------------------------------------------

    def register_rider(self, rider: Rider) -> None:
        self.riders[rider.id] = rider

    def register_shelter(self, shelter: Shelter) -> None:
        self.shelters[shelter.id] = shelter

    def register_standby(self, lot_id: str, standby_rider_id: str) -> None:
        """Register a standby rider ready to take over a specific lot."""
        self.standbys[lot_id] = standby_rider_id

    # -----------------------------------------------------------------------
    # Batch Posting & Chunking
    # -----------------------------------------------------------------------

    def post_batch(
        self,
        batch: Batch,
        now_minutes: float = 0.0,
        traffic_multiplier: float = 1.0,
    ) -> tuple[RescuePlan, list[Lot]]:
        """
        Post a new batch from a donor.
        Calculates the rescue plan, emits surge broadcast if short,
        and creates Lot objects for the rescuable chunks.
        """
        if getattr(batch, "created_at_minutes", 0.0) == 0.0 and now_minutes > 0.0:
            batch.created_at_minutes = now_minutes

        self.batches[batch.id] = batch
        self.event_log.append(
            "batch_created",
            {"batch_id": batch.id, "item": batch.item, "quantity": batch.quantity},
            reason=f"Batch {batch.id} posted: {batch.quantity} units of {batch.item}",
        )

        plan = calculate_rescue_plan(
            batch,
            list(self.riders.values()),
            list(self.shelters.values()),
            now_minutes=now_minutes,
            traffic_multiplier=traffic_multiplier,
        )

        created_lots: list[Lot] = []
        for chunk in plan.chunks:
            lot = Lot(
                batch_id=batch.id,
                quantity=chunk.units,
                state=LotState.PENDING,
                assigned_rider_id=None,  # pending rider acceptance
                target_shelter_id=chunk.shelter_id,
            )
            self.lots[lot.id] = lot
            created_lots.append(lot)

        if plan.surge_broadcast:
            self.event_log.append(
                "surge_broadcast",
                {
                    "batch_id": batch.id,
                    "unrescued_units": plan.unrescued_units,
                    "needed_riders": plan.needed_riders,
                    "pnr_minutes": plan.pnr_minutes,
                },
                reason=f"Surge broadcast: {plan.unrescued_units} units at risk! Urgency PNR: {plan.pnr_minutes}m",
            )

        self.event_log.append(
            "plan_updated",
            {
                "batch_id": batch.id,
                "rescuable_units": plan.rescuable_units,
                "unrescued_units": plan.unrescued_units,
                "chunks_count": len(created_lots),
            },
            reason=plan.reason,
        )

        return plan, created_lots

    # -----------------------------------------------------------------------
    # Lease Management (Accept / Release / Expire)
    # -----------------------------------------------------------------------

    def accept_lot(
        self,
        lot_id: str,
        rider_id: str,
        now_minutes: float = 0.0,
        expected_version: Optional[int] = None,
        traffic_multiplier: float = 1.0,
    ) -> Lease:
        """
        Rider accepts a lot claim, beginning a time-limited Lease.
        Uses versioned compare-and-set for concurrency safety.
        Infeasible claims (negative slack) are hard-rejected.
        """
        lot = self.lots.get(lot_id)
        if not lot:
            raise ValueError(f"Lot {lot_id} does not exist")

        if lot.state != LotState.PENDING:
            raise ValueError(f"Lot {lot_id} is already in state {lot.state.value}")

        if expected_version is not None and lot.version != expected_version:
            raise ValueError(
                f"Version conflict: lot version is {lot.version}, expected {expected_version}"
            )

        rider = self.riders.get(rider_id)
        if not rider:
            raise ValueError(f"Rider {rider_id} does not exist")

        batch = self.batches.get(lot.batch_id)
        if not batch:
            raise ValueError(f"Batch {lot.batch_id} not found")

        shelter = self.shelters.get(lot.target_shelter_id) if lot.target_shelter_id else None
        if not shelter:
            raise ValueError("Target shelter not assigned to lot")

        # Invariant: no negative slack allowed
        sl = calculate_chunk_slack(batch, rider, shelter, now_minutes, traffic_multiplier)
        if sl < 0:
            reason = f"Rider {rider.name} claim rejected: slack is negative ({sl:.1f}m)"
            self.event_log.append("claim_rejected", {"lot_id": lot_id, "rider_id": rider_id}, reason=reason)
            raise ValueError(reason)

        ttl_sec = adaptive_lease_ttl_seconds(sl)
        ttl_minutes = ttl_sec / 60.0

        lease = Lease(
            lot_id=lot.id,
            rider_id=rider.id,
            state=LeaseState.ACTIVE,
            ttl_seconds=ttl_sec,
            expires_at=now_minutes + ttl_minutes,  # stored in simulated minutes
        )
        self.leases[lease.id] = lease

        # Transition state atomically
        lot.state = LotState.ASSIGNED
        lot.assigned_rider_id = rider.id
        lot.version += 1

        rider.status = RiderStatus.BUSY
        shelter.inbound += lot.quantity

        self.event_log.append(
            "lease_started",
            {
                "lease_id": lease.id,
                "lot_id": lot.id,
                "rider_id": rider.id,
                "ttl_seconds": ttl_sec,
                "expires_at_minutes": lease.expires_at,
            },
            reason=f"Rider {rider.name} claimed Lot {lot.id} (Lease TTL: {ttl_sec}s derived from {sl:.1f}m slack)",
        )

        return lease

    def release_lot(
        self,
        lot_id: str,
        rider_id: str,
        now_minutes: float = 0.0,
    ) -> Lot:
        """
        Rider releases claim early: penalty-free!
        Lot returns to PENDING or is immediately taken by standby rider.
        """
        lot = self.lots.get(lot_id)
        if not lot or lot.assigned_rider_id != rider_id:
            raise ValueError(f"Lot {lot_id} not assigned to rider {rider_id}")

        rider = self.riders.get(rider_id)
        if rider:
            rider.status = RiderStatus.AVAILABLE

        # Mark active lease as released
        for lease in self.leases.values():
            if lease.lot_id == lot_id and lease.state == LeaseState.ACTIVE:
                lease.state = LeaseState.RELEASED

        shelter = self.shelters.get(lot.target_shelter_id)
        if shelter:
            shelter.inbound = max(shelter.inbound - lot.quantity, 0)

        lot.assigned_rider_id = None
        lot.state = LotState.PENDING
        lot.version += 1

        self.event_log.append(
            "lot_released",
            {"lot_id": lot.id, "rider_id": rider_id},
            reason=f"Rider {rider.name if rider else rider_id} released Lot {lot.id} early (penalty-free)",
        )

        # Check for standby takeover
        self._check_standby_takeover(lot, now_minutes)
        return lot

    def expire_leases(self, now_minutes: float) -> list[Lease]:
        """
        Scan active leases. If now >= expires_at without progress/pickup,
        the lease expires early, and a standby rider takes over.
        """
        expired: list[Lease] = []

        for lease in list(self.leases.values()):
            if lease.state == LeaseState.ACTIVE and lease.expires_at is not None:
                if now_minutes >= lease.expires_at:
                    lease.state = LeaseState.EXPIRED
                    expired.append(lease)

                    lot = self.lots.get(lease.lot_id)
                    silent_rider = self.riders.get(lease.rider_id)
                    if silent_rider:
                        silent_rider.status = RiderStatus.SILENT_YELLOW

                    self.event_log.append(
                        "lease_expired",
                        {
                            "lease_id": lease.id,
                            "lot_id": lease.lot_id,
                            "rider_id": lease.rider_id,
                            "expired_at_minutes": now_minutes,
                        },
                        reason=f"Lease {lease.id} expired: Rider {silent_rider.name if silent_rider else lease.rider_id} went silent before pickup",
                    )

                    if lot:
                        shelter = self.shelters.get(lot.target_shelter_id)
                        if shelter:
                            shelter.inbound = max(shelter.inbound - lot.quantity, 0)

                        lot.assigned_rider_id = None
                        lot.state = LotState.PENDING
                        lot.version += 1

                        self._check_standby_takeover(lot, now_minutes)

        return expired

    def _check_standby_takeover(self, lot: Lot, now_minutes: float) -> bool:
        """Helper to transfer lot to pre-assigned standby rider if available."""
        standby_id = self.standbys.get(lot.id)
        if not standby_id:
            return False

        standby_rider = self.riders.get(standby_id)
        if standby_rider and standby_rider.status == RiderStatus.AVAILABLE:
            try:
                new_lease = self.accept_lot(lot.id, standby_id, now_minutes=now_minutes)
                self.event_log.append(
                    "standby_takeover",
                    {
                        "lot_id": lot.id,
                        "standby_rider_id": standby_id,
                        "lease_id": new_lease.id,
                    },
                    reason=f"Standby rider {standby_rider.name} immediately took over Lot {lot.id}; zero food lost",
                )
                return True
            except ValueError:
                return False
        return False

    # -----------------------------------------------------------------------
    # Journey State Transitions
    # -----------------------------------------------------------------------

    def mark_picked_up(self, lot_id: str, now_minutes: float = 0.0) -> Lot:
        """Mark lot as picked up by the assigned rider."""
        lot = self.lots.get(lot_id)
        if not lot:
            raise ValueError(f"Lot {lot_id} does not exist")
        if lot.state != LotState.ASSIGNED:
            raise ValueError(f"Cannot pick up lot in state {lot.state.value}")

        lot.state = LotState.PICKED_UP
        lot.version += 1

        self.event_log.append(
            "lot_picked_up",
            {"lot_id": lot.id, "rider_id": lot.assigned_rider_id},
            reason=f"Lot {lot.id} picked up by rider {lot.assigned_rider_id}",
        )
        return lot

    def mark_delivered(self, lot_id: str, now_minutes: float = 0.0) -> Lot:
        """Mark lot as delivered to the destination shelter."""
        lot = self.lots.get(lot_id)
        if not lot:
            raise ValueError(f"Lot {lot_id} does not exist")
        if lot.state not in (LotState.ASSIGNED, LotState.PICKED_UP):
            raise ValueError(f"Cannot deliver lot in state {lot.state.value}")

        lot.state = LotState.DELIVERED
        lot.version += 1

        rider = self.riders.get(lot.assigned_rider_id)
        if rider:
            rider.status = RiderStatus.AVAILABLE

        shelter = self.shelters.get(lot.target_shelter_id)
        if shelter:
            shelter.inbound = max(shelter.inbound - lot.quantity, 0)
            shelter.delivered += lot.quantity

        for lease in self.leases.values():
            if lease.lot_id == lot_id and lease.state == LeaseState.ACTIVE:
                lease.state = LeaseState.COMPLETED

        self.event_log.append(
            "lot_delivered",
            {"lot_id": lot.id, "shelter_id": lot.target_shelter_id, "units": lot.quantity},
            reason=f"Lot {lot.id} ({lot.quantity} units) safely delivered to {shelter.name if shelter else lot.target_shelter_id}",
        )
        return lot
