"""
Scenario 5 – Vanishing Rider (Silent After Pickup)

Setup from Project Document (Section 14, 15, 19):
- Batch of 80 meals split across two riders (40 each) to limit risk.
- Both riders pick up their respective lots.
- Rider 1 completes delivery normally.
- Rider 2 goes silent after pickup and never arrives.
- Expected system behavior:
  * Multi-stage escalation:
      🟡 Yellow (suspect): Missed heartbeat (~2 min) -> contact attempted.
      🟠 Orange (at risk): Past ETA + grace -> backup courier alerted.
      🔴 Red (declared lost): Slack exhausted -> write off single chunk honestly.
  * Damage containment: Only 40 meals lost, NOT the full 80 meals.
  * Never starve the shelter: Shelter demand is immediately reopened so other aid can be sent.
  * Honest loss recorded in event log.
  * Rider's reliability score is reduced.
"""

import pytest
from engine.models import Batch, Rider, Shelter, LotState, RiderStatus
from engine.dispatcher import Dispatcher
from engine.reroute import ResilienceManager, evaluate_silent_rider_health
from engine.event_log import EventLog


def test_scenario_5_vanishing_rider():
    event_log = EventLog()
    dispatcher = Dispatcher(event_log=event_log)
    resilience = ResilienceManager(event_log=event_log)

    # 1. Setup Donated Batch of 80 meals, Shelter capacity 100
    batch = Batch(
        item="cooked_vegetable_meals",
        quantity=80,
        expiry_minutes=60,
        donor_lat=28.6139,
        donor_lon=77.2090,
    )
    shelter = Shelter(id="s1", name="City Shelter", capacity=100, lat=28.6250, lon=77.2150)
    dispatcher.register_shelter(shelter)

    rider_good = Rider(id="r1", name="Rider Reliable", capacity=40, lat=28.6120, lon=77.2050)
    rider_flaky = Rider(id="r2", name="Rider Vanishing", capacity=40, lat=28.6130, lon=77.2060, reliability=0.9)
    dispatcher.register_rider(rider_good)
    dispatcher.register_rider(rider_flaky)

    # 2. Post batch -> 2 lots of 40 units each
    plan, lots = dispatcher.post_batch(batch, now_minutes=0.0)
    assert len(lots) == 2
    lot1, lot2 = lots[0], lots[1]

    # Both riders accept and pick up
    dispatcher.accept_lot(lot1.id, rider_good.id, now_minutes=0.0)
    dispatcher.accept_lot(lot2.id, rider_flaky.id, now_minutes=0.0)

    dispatcher.mark_picked_up(lot1.id, now_minutes=5.0)
    dispatcher.mark_picked_up(lot2.id, now_minutes=5.0)

    assert shelter.inbound == 80

    # 3. Rider 1 delivers reliably at t=20.0
    dispatcher.mark_delivered(lot1.id, now_minutes=20.0)
    assert lot1.state == LotState.DELIVERED
    assert shelter.delivered == 40
    assert shelter.inbound == 40

    # 4. Rider 2 VANISHES after pickup:
    # --- STAGE 1: Yellow (2 minutes without ping) ---
    stage_yellow = evaluate_silent_rider_health(
        minutes_since_ping=2.5,
        now_minutes=15.0,
        expected_arrival_minutes=20.0,
        slack_minutes=35.0,
    )
    assert stage_yellow == RiderStatus.SILENT_YELLOW
    resilience.escalate_silent_rider(rider_flaky, lot2, shelter, stage_yellow, now_minutes=15.0)
    assert rider_flaky.status == RiderStatus.SILENT_YELLOW

    # --- STAGE 2: Orange (Past expected arrival + grace) ---
    stage_orange = evaluate_silent_rider_health(
        minutes_since_ping=6.0,
        now_minutes=25.0,
        expected_arrival_minutes=20.0,  # was due 5 min ago
        slack_minutes=25.0,
    )
    assert stage_orange == RiderStatus.SILENT_ORANGE
    resilience.escalate_silent_rider(rider_flaky, lot2, shelter, stage_orange, now_minutes=25.0)
    assert rider_flaky.status == RiderStatus.SILENT_ORANGE

    # --- STAGE 3: Red (Slack nearly exhausted, <= 5 min) ---
    initial_reliability = rider_flaky.reliability
    stage_red = evaluate_silent_rider_health(
        minutes_since_ping=35.0,
        now_minutes=55.0,
        expected_arrival_minutes=20.0,
        slack_minutes=3.0,  # only 3 min left before food is spoiled!
    )
    assert stage_red == RiderStatus.SILENT_RED
    resilience.escalate_silent_rider(rider_flaky, lot2, shelter, stage_red, now_minutes=55.0)

    # 5. Assert Damage Containment & Invariants:
    # - Only chunk 2 is lost
    assert lot2.state == LotState.LOST
    assert lot1.state == LotState.DELIVERED  # untouched!

    # - Shelter demand reopened immediately (inbound deducted so new food can be ordered)
    assert shelter.inbound == 0
    assert shelter.delivered == 40

    # - Rider reliability penalized
    assert rider_flaky.reliability < initial_reliability

    # - Honest audit record written to event log
    lost_events = event_log.by_type("lot_lost")
    assert len(lost_events) == 1
    assert lost_events[0].payload["units_lost"] == 40
    assert "declared lost after pickup" in lost_events[0].reason
