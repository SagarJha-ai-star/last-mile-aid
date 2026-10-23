"""
Scenario 3 – Pulled Rug

Setup from Project Document (Section 14, 15, 19):
- Batch of 40 meals is picked up by Rider 1 targeting Shelter A.
- While Rider 1 is mid-route (in-transit), Shelter A suddenly fills or closes ('pulled rug').
- Expected system behavior:
  * Shelter closure is detected immediately.
  * System queries fallback chain from rider's LIVE position (floating supply node).
  * Instant reroute to the next feasible shelter (Shelter B) with positive slack.
  * Inbound demand transfer: deducted from Shelter A, added to Shelter B.
  * "Why?" decision log provides a clear human-readable explanation.
  * Invariants hold throughout.
"""

import pytest
from engine.models import Batch, Rider, Shelter, LotState, RiderStatus
from engine.dispatcher import Dispatcher
from engine.reroute import ResilienceManager
from engine.event_log import EventLog


def test_scenario_3_pulled_rug():
    event_log = EventLog()
    dispatcher = Dispatcher(event_log=event_log)
    resilience = ResilienceManager(event_log=event_log)

    # 1. Setup Donor Batch, Shelter A (initial target), Shelter B (fallback)
    batch = Batch(
        item="fresh_boxed_lunches",
        quantity=40,
        expiry_minutes=90,
        donor_lat=28.6139,
        donor_lon=77.2090,
    )
    shelter_a = Shelter(id="sa", name="Shelter A", capacity=50, lat=28.6300, lon=77.2100)
    shelter_b = Shelter(id="sb", name="Shelter B (Fallback)", capacity=50, lat=28.6100, lon=77.2300)

    dispatcher.register_shelter(shelter_a)
    dispatcher.register_shelter(shelter_b)

    rider = Rider(id="r1", name="Rider Vikrant", capacity=40, lat=28.6139, lon=77.2090)
    dispatcher.register_rider(rider)

    # 2. Post batch and accept lot
    plan, lots = dispatcher.post_batch(batch, now_minutes=0.0)
    lot = lots[0]
    dispatcher.accept_lot(lot.id, rider.id, now_minutes=0.0)

    # 3. Rider picks up cargo and begins transit
    dispatcher.mark_picked_up(lot.id, now_minutes=5.0)
    assert lot.state == LotState.PICKED_UP
    assert shelter_a.inbound == 40
    assert shelter_b.inbound == 0

    # 4. Rider is mid-route at live GPS coordinates
    rider.lat = 28.6220
    rider.lon = 28.6220
    rider.lon = 77.2100

    # 5. CHAOS: Shelter A suddenly closes while rider is mid-transit!
    reroute_results = resilience.handle_shelter_closed_or_full(
        closed_shelter_id=shelter_a.id,
        lots=dispatcher.lots,
        riders=dispatcher.riders,
        batches=dispatcher.batches,
        shelters=dispatcher.shelters,
        now_minutes=15.0,
    )

    # 6. Verify instant reroute
    assert len(reroute_results) == 1
    result = reroute_results[0]
    assert result.success is True
    assert result.old_shelter_id == "sa"
    assert result.new_shelter_id == "sb"
    assert result.slack_minutes > 0
    assert "Instant reroute to Shelter B" in result.reason

    # Verify lot target is now Shelter B
    assert lot.target_shelter_id == "sb"
    assert shelter_a.inbound == 0
    assert shelter_b.inbound == 40

    # Verify event log audit trail
    reroute_events = event_log.by_type("rerouted")
    assert len(reroute_events) == 1
    assert reroute_events[0].payload["to_shelter"] == "sb"

    # 7. Rider arrives at Shelter B and completes delivery
    dispatcher.mark_delivered(lot.id, now_minutes=30.0)
    assert lot.state == LotState.DELIVERED
    assert shelter_b.delivered == 40
    assert shelter_b.inbound == 0
