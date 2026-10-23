"""
Scenario 4 – Unsafe Match (Safety Rule Engine)

Setup from Project Document (Section 14, 15, 19):
- Donor posts a batch of infant formula (tagged: "infant_formula").
- Shelter A lacks clean water supply (has only "refrigeration").
- Expected system behavior:
  * Hard-blocks the assignment to Shelter A.
  * Displays a visible, human-readable reason:
    "Infant formula requires a shelter with clean water supply".
  * When Shelter B (with clean water) is available, safe match is accepted.
  * Invariant: No unsafe match is ever permitted into active delivery.
"""

import pytest
from engine.models import Batch, Rider, Shelter, LotState
from engine.rules import check_safety
from engine.dispatcher import Dispatcher
from engine.event_log import EventLog


def test_scenario_4_unsafe_match_hard_blocked():
    event_log = EventLog()
    dispatcher = Dispatcher(event_log=event_log)

    # 1. Infant formula batch requiring clean water
    batch = Batch(
        item="infant_formula",
        quantity=50,
        expiry_minutes=90,
        tags=["infant_formula"],
        donor_lat=28.6139,
        donor_lon=77.2090,
    )

    # 2. Shelter A has NO clean water
    shelter_a = Shelter(
        id="sa",
        name="Shelter Without Water",
        capacity=100,
        capabilities=["refrigeration"],  # missing clean_water!
        lat=28.6200,
        lon=77.2100,
    )
    rider = Rider(id="r1", name="Rider Kabir", capacity=50, lat=28.6150, lon=77.2080)

    dispatcher.register_shelter(shelter_a)
    dispatcher.register_rider(rider)

    # 3. Direct safety rule evaluation must fail with explicit reason
    safety_check = check_safety(batch.tags, shelter_a.capabilities)
    assert safety_check.allowed is False
    assert len(safety_check.reasons) == 1
    assert "clean water" in safety_check.reasons[0].lower()

    # 4. Dispatcher post_batch must refuse to match with Shelter A
    plan, lots = dispatcher.post_batch(batch, now_minutes=0.0)
    assert plan.rescuable_units == 0
    assert plan.unrescued_units == 50
    assert len(lots) == 0

    # 5. Now register Shelter B which DOES have clean water
    shelter_b = Shelter(
        id="sb",
        name="Shelter With Clean Water",
        capacity=100,
        capabilities=["clean_water", "refrigeration"],
        lat=28.6250,
        lon=77.2150,
    )
    dispatcher.register_shelter(shelter_b)

    # 6. Re-plan: Safe match is accepted immediately to Shelter B
    safe_plan, safe_lots = dispatcher.post_batch(batch, now_minutes=1.0)
    assert safe_plan.rescuable_units == 50
    assert len(safe_lots) == 1
    assert safe_lots[0].target_shelter_id == "sb"

    # 7. Rider accepts safe lot
    lease = dispatcher.accept_lot(safe_lots[0].id, rider.id, now_minutes=1.0)
    assert lease is not None
    assert safe_lots[0].state == LotState.ASSIGNED
    assert shelter_b.inbound == 50
    assert shelter_a.inbound == 0
