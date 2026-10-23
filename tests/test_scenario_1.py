"""
Scenario 1 – Banquet Rush

Setup from Project Document (Section 14, 15, 19):
- 180 meals donated, expiring in 75 minutes.
- Volunteer riders carry up to 40 meals each.
- Shelters hold up to 50 meals each. (3 shelters = 150 capacity).
- Expected system behavior:
  * Calculates and displays exact rescuable count (150 meals).
  * 30 meals remain unrescued (identifying shortage).
  * Splits the 150 rescuable meals into rider-sized chunks across 4 riders.
  * Surge-broadcasts to alert all available riders because plan is short.
  * PNR countdown computed to display urgency.
  * Invariants hold throughout.
"""

import pytest
from engine.models import Batch, Rider, Shelter, LotState
from engine.dispatcher import Dispatcher, calculate_rescue_plan
from engine.event_log import EventLog


def test_scenario_1_banquet_rush():
    event_log = EventLog()
    dispatcher = Dispatcher(event_log=event_log)

    # 1. Create Donated Batch: 180 meals, 75 min expiry
    batch = Batch(
        item="cooked_banquet_meals",
        quantity=180,
        expiry_minutes=75,
        donor_lat=28.6139,
        donor_lon=77.2090,  # Central Delhi
    )

    # 2. Shelters hold up to 50 meals each, total demand = 150
    shelters = [
        Shelter(id="s1", name="Shelter North", capacity=40, lat=28.6300, lon=77.2100),
        Shelter(id="s2", name="Shelter East", capacity=40, lat=28.6150, lon=77.2300),
        Shelter(id="s3", name="Shelter South", capacity=40, lat=28.5900, lon=77.2050),
        Shelter(id="s4", name="Shelter West", capacity=30, lat=28.6100, lon=77.1900),
    ]
    for s in shelters:
        dispatcher.register_shelter(s)

    # 3. Available riders carry up to 40 meals each
    riders = [
        Rider(id="r1", name="Rider 1", capacity=40, lat=28.6100, lon=77.2000),
        Rider(id="r2", name="Rider 2", capacity=40, lat=28.6120, lon=77.2050),
        Rider(id="r3", name="Rider 3", capacity=40, lat=28.6180, lon=77.2120),
        Rider(id="r4", name="Rider 4", capacity=40, lat=28.6200, lon=77.2150),
    ]
    for r in riders:
        dispatcher.register_rider(r)

    # 4. Post batch and generate rescue plan
    plan, lots = dispatcher.post_batch(batch, now_minutes=0.0)

    # Assertions per Section 14 & 19 of project doc:
    # - Rescuable count: 150 meals
    assert plan.rescuable_units == 150, f"Expected 150 rescuable units, got {plan.rescuable_units}"
    assert plan.unrescued_units == 30, f"Expected 30 unrescued units, got {plan.unrescued_units}"

    # - Needs 4 riders (4 chunks: e.g. 40, 40, 40, 30)
    assert plan.needed_riders == 4
    assert len(lots) == 4
    assert sum(lot.quantity for lot in lots) == 150

    # - Chunk sizing respects rider capacity
    for chunk in plan.chunks:
        assert chunk.units <= 40
        assert chunk.slack_minutes > 0
        assert chunk.lease_ttl_seconds >= 180  # min clamp is 3 min = 180s

    # - Surge broadcast triggered because batch has unrescued units
    assert plan.surge_broadcast is True
    surge_events = event_log.by_type("surge_broadcast")
    assert len(surge_events) == 1
    assert surge_events[0].payload["unrescued_units"] == 30

    # - PNR is positive and before expiry
    assert 0 < plan.pnr_minutes < batch.expiry_minutes

    # - All lots created in PENDING state
    for lot in lots:
        assert lot.state == LotState.PENDING
        assert lot.assigned_rider_id is None
