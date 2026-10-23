"""
Scenario 2 – Ghost Rider

Setup from Project Document (Section 14, 15, 19):
- A batch is posted and chunked into lots.
- Rider 1 accepts Lot 1, starting an adaptive lease with TTL derived from slack.
- A standby rider (Rider 2) is pre-assigned / available.
- Rider 1 goes silent before pickup (no heartbeat, no progress).
- Simulated time advances past the lease TTL.
- The lease expires early, and the standby rider immediately takes over.
- Standby rider picks up and completes delivery.
- Result: 0 food lost, demand fulfilled!
"""

import pytest
from engine.models import Batch, Rider, Shelter, LotState, LeaseState, RiderStatus
from engine.dispatcher import Dispatcher
from engine.event_log import EventLog


def test_scenario_2_ghost_rider():
    event_log = EventLog()
    dispatcher = Dispatcher(event_log=event_log)

    # 1. Setup Donor Batch, Shelter, and Riders
    batch = Batch(
        item="fresh_curry_and_rice",
        quantity=40,
        expiry_minutes=60,
        donor_lat=28.6139,
        donor_lon=77.2090,
    )
    shelter = Shelter(id="s1", name="Community Kitchen", capacity=50, lat=28.6250, lon=77.2150)
    dispatcher.register_shelter(shelter)

    primary_rider = Rider(id="r1", name="Ghost Rider (Aarav)", capacity=40, lat=28.6120, lon=77.2050)
    standby_rider = Rider(id="r2", name="Standby Rider (Priya)", capacity=40, lat=28.6110, lon=77.2040)
    dispatcher.register_rider(primary_rider)
    dispatcher.register_rider(standby_rider)

    # 2. Post Batch -> Creates 1 Lot of 40 units
    plan, lots = dispatcher.post_batch(batch, now_minutes=0.0)
    assert len(lots) == 1
    lot = lots[0]

    # Pre-register standby rider for this lot
    dispatcher.register_standby(lot.id, standby_rider.id)

    # 3. Primary rider accepts lot at t=0.0
    lease = dispatcher.accept_lot(lot.id, primary_rider.id, now_minutes=0.0)
    assert lease.state == LeaseState.ACTIVE
    assert lot.state == LotState.ASSIGNED
    assert lot.assigned_rider_id == primary_rider.id
    assert primary_rider.status == RiderStatus.BUSY

    # Verify TTL is positive and derived from slack
    ttl_seconds = lease.ttl_seconds
    assert ttl_seconds > 0
    ttl_minutes = ttl_seconds / 60.0
    assert lease.expires_at == pytest.approx(ttl_minutes, rel=1e-3)

    # 4. Primary rider goes silent! Time advances beyond TTL without pickup
    now_simulated = ttl_minutes + 1.0  # past expiry
    expired_leases = dispatcher.expire_leases(now_minutes=now_simulated)

    assert len(expired_leases) == 1
    assert expired_leases[0].id == lease.id
    assert expired_leases[0].state == LeaseState.EXPIRED

    # 5. Check ghost rider was flagged and standby immediately took over
    assert primary_rider.status == RiderStatus.SILENT_YELLOW

    # Verify standby takeover occurred without food loss
    assert lot.assigned_rider_id == standby_rider.id
    assert lot.state == LotState.ASSIGNED

    standby_takeover_events = event_log.by_type("standby_takeover")
    assert len(standby_takeover_events) == 1
    assert standby_takeover_events[0].payload["standby_rider_id"] == standby_rider.id

    # 6. Standby rider successfully picks up and delivers
    dispatcher.mark_picked_up(lot.id, now_minutes=now_simulated + 2.0)
    assert lot.state == LotState.PICKED_UP

    dispatcher.mark_delivered(lot.id, now_minutes=now_simulated + 10.0)
    assert lot.state == LotState.DELIVERED
    assert shelter.delivered == 40
    assert standby_rider.status == RiderStatus.AVAILABLE

    # Zero food lost
    assert lot.state != LotState.LOST
