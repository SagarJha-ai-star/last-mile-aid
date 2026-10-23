"""
Tests for BotRiderAgent autonomous navigation simulation.
"""

from engine.models import Batch, Rider, Shelter, LotState
from engine.dispatcher import Dispatcher
from engine.event_log import EventLog
from sim.bot_riders import BotRiderAgent


def test_bot_rider_end_to_end():
    log = EventLog()
    dispatcher = Dispatcher(event_log=log)

    shelter = Shelter(
        id="s_test",
        name="Test Shelter",
        lat=28.6328,
        lon=77.2197,
        capacity=50,
        capabilities=["clean_water"],
    )
    dispatcher.register_shelter(shelter)

    rider = Rider(
        id="r_bot",
        name="Bot Courier",
        lat=28.6180,
        lon=77.2100,
        capacity=40,
    )
    dispatcher.register_rider(rider)

    batch = Batch(
        id="b_test",
        item="Test Meals",
        quantity=40,
        expiry_minutes=60,
        donor_lat=28.6200,
        donor_lon=77.2120,
    )
    plan, lots = dispatcher.post_batch(batch, now_minutes=0.0)
    lot = lots[0]

    dispatcher.accept_lot(lot.id, "r_bot", now_minutes=0.0)

    agent = BotRiderAgent(rider_id="r_bot", speed_kmh=120.0)  # high speed for fast step in test

    # Step 1: Detects assigned lot and heads to donor
    agent.step(dispatcher, delta_minutes=1.0, event_log=log)
    assert agent.phase in ["to_donor", "to_shelter"]

    # Step until cargo picked up and heading to shelter
    for _ in range(10):
        agent.step(dispatcher, delta_minutes=2.0, event_log=log)
        if agent.phase == "to_shelter" or lot.state == LotState.PICKED_UP:
            break

    assert lot.state in [LotState.PICKED_UP, LotState.DELIVERED]

    # Step until cargo delivered
    for _ in range(10):
        agent.step(dispatcher, delta_minutes=2.0, event_log=log)
        if lot.state == LotState.DELIVERED:
            break

    assert lot.state == LotState.DELIVERED
    assert agent.phase == "idle"
