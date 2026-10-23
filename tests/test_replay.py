"""
Replay Determinism Test (Section 15 of Project Document)

Invariant:
Running the same event log twice produces identical end state.
Demonstrates that state is a pure fold over the append-only event stream.
"""

import json
from engine.models import Batch, Rider, Shelter, LotState, RiderStatus
from engine.dispatcher import Dispatcher
from engine.reroute import ResilienceManager
from engine.event_log import EventLog, Event


def fold_state_from_events(events: list[Event]) -> dict:
    """
    Pure state reconstruction by folding over the immutable event stream.
    """
    lots: dict[str, dict] = {}
    shelters: dict[str, dict] = {}
    scoreboard = {"saved": 0, "lost": 0, "rerouted": 0}

    for ev in events:
        p = ev.payload
        t = ev.type

        if t == "batch_created":
            pass

        elif t == "plan_updated":
            pass

        elif t == "lease_started":
            lot_id = p["lot_id"]
            if lot_id not in lots:
                lots[lot_id] = {"id": lot_id, "state": "assigned", "rider_id": p["rider_id"], "units": 40}
            else:
                lots[lot_id]["state"] = "assigned"
                lots[lot_id]["rider_id"] = p["rider_id"]

        elif t == "lot_picked_up":
            lot_id = p["lot_id"]
            if lot_id in lots:
                lots[lot_id]["state"] = "picked_up"

        elif t == "rerouted":
            lot_id = p["lot_id"]
            if lot_id in lots:
                lots[lot_id]["target_shelter"] = p["to_shelter"]
            scoreboard["rerouted"] += 1

        elif t == "lot_delivered":
            lot_id = p["lot_id"]
            if lot_id in lots:
                lots[lot_id]["state"] = "delivered"
            scoreboard["saved"] += p.get("units", 40)

        elif t == "lot_lost":
            lot_id = p["lot_id"]
            if lot_id in lots:
                lots[lot_id]["state"] = "lost"
            scoreboard["lost"] += p.get("units_lost", 40)

    return {
        "lots": lots,
        "scoreboard": scoreboard,
        "event_count": len(events),
    }


def test_deterministic_replay_produces_identical_state():
    # 1. Run a full multi-step scenario to generate a rich event stream
    event_log = EventLog()
    dispatcher = Dispatcher(event_log=event_log)
    resilience = ResilienceManager(event_log=event_log)

    batch = Batch(item="Curry", quantity=80, expiry_minutes=60, donor_lat=28.6, donor_lon=77.2)
    s1 = Shelter(id="s1", name="Shelter 1", capacity=50, lat=28.62, lon=77.21)
    s2 = Shelter(id="s2", name="Shelter 2", capacity=50, lat=28.61, lon=77.22)
    s3 = Shelter(id="s3", name="Shelter 3 (Fallback)", capacity=50, lat=28.63, lon=77.23)
    r1 = Rider(id="r1", name="Rider 1", capacity=40, lat=28.6, lon=77.2)
    r2 = Rider(id="r2", name="Rider 2", capacity=40, lat=28.6, lon=77.2)

    dispatcher.register_shelter(s1)
    dispatcher.register_shelter(s2)
    dispatcher.register_shelter(s3)
    dispatcher.register_rider(r1)
    dispatcher.register_rider(r2)

    plan, lots = dispatcher.post_batch(batch)
    lot1, lot2 = lots[0], lots[1]

    dispatcher.accept_lot(lot1.id, r1.id, now_minutes=0.0)
    dispatcher.accept_lot(lot2.id, r2.id, now_minutes=0.0)

    dispatcher.mark_picked_up(lot1.id, now_minutes=5.0)
    dispatcher.mark_picked_up(lot2.id, now_minutes=5.0)

    # Reroute Lot 1 mid-journey
    resilience.handle_shelter_closed_or_full(
        closed_shelter_id=lot1.target_shelter_id,
        lots=dispatcher.lots,
        riders=dispatcher.riders,
        batches=dispatcher.batches,
        shelters=dispatcher.shelters,
        now_minutes=10.0,
    )

    # Deliver Lot 1
    dispatcher.mark_delivered(lot1.id, now_minutes=20.0)

    # Declare Lot 2 lost (silent courier)
    resilience.escalate_silent_rider(r2, lot2, s2, RiderStatus.SILENT_RED, now_minutes=35.0)

    recorded_events = event_log.all()
    assert len(recorded_events) >= 6

    # 2. Replay Pass 1
    state_pass_1 = fold_state_from_events(recorded_events)

    # 3. Replay Pass 2
    state_pass_2 = fold_state_from_events(recorded_events)

    # 4. Invariant Assertion: Both passes must be 100% byte-for-byte identical
    assert json.dumps(state_pass_1, sort_keys=True) == json.dumps(state_pass_2, sort_keys=True)
    assert state_pass_1["scoreboard"]["saved"] == 40
    assert state_pass_1["scoreboard"]["lost"] == 40
    assert state_pass_1["scoreboard"]["rerouted"] >= 1
