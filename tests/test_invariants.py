"""
Invariant tests – guaranteed across all operations.

Invariants tested always (Section 15 of Project Document):
1. No lot is claimed by two riders simultaneously.
2. Sum of lots never exceeds the batch total.
3. No unsafe match is accepted.
4. No assignment with negative slack is allowed.
"""

import pytest
from engine.models import Batch, Rider, Shelter, LotState
from engine.dispatcher import Dispatcher, calculate_chunk_slack
from engine.event_log import EventLog


class TestInvariants:

    def test_invariant_no_lot_claimed_twice(self):
        """Invariant 1: A lot cannot be claimed by two riders simultaneously."""
        dispatcher = Dispatcher()
        batch = Batch(item="meals", quantity=40, expiry_minutes=60, donor_lat=28.6, donor_lon=77.2)
        shelter = Shelter(id="s1", name="Shelter", capacity=50, lat=28.61, lon=77.21)
        rider1 = Rider(id="r1", name="Rider 1", capacity=40, lat=28.6, lon=77.2)
        rider2 = Rider(id="r2", name="Rider 2", capacity=40, lat=28.6, lon=77.2)

        dispatcher.register_shelter(shelter)
        dispatcher.register_rider(rider1)
        dispatcher.register_rider(rider2)

        _, lots = dispatcher.post_batch(batch)
        lot = lots[0]

        # First claim succeeds
        lease1 = dispatcher.accept_lot(lot.id, rider1.id, expected_version=lot.version)
        assert lease1 is not None
        assert lot.assigned_rider_id == rider1.id

        # Second claim must be rejected (version mismatch or already claimed)
        with pytest.raises(ValueError, match="already in state"):
            dispatcher.accept_lot(lot.id, rider2.id)

    def test_invariant_sum_of_lots_never_exceeds_batch(self):
        """Invariant 2: Sum of chunk quantities never exceeds the batch total."""
        dispatcher = Dispatcher()
        batch = Batch(item="meals", quantity=100, expiry_minutes=90, donor_lat=28.6, donor_lon=77.2)
        shelter = Shelter(id="s1", capacity=200, lat=28.61, lon=77.21)
        dispatcher.register_shelter(shelter)

        # 5 riders with capacity 30 each (total capacity 150)
        for i in range(5):
            dispatcher.register_rider(
                Rider(id=f"r{i}", capacity=30, lat=28.6, lon=77.2)
            )

        plan, lots = dispatcher.post_batch(batch)
        total_lot_units = sum(lot.quantity for lot in lots)

        assert total_lot_units <= batch.quantity
        assert total_lot_units == 100  # fully chunked without exceeding

    def test_invariant_no_unsafe_match_accepted(self):
        """Invariant 3: Safety rules are strictly enforced — unsafe matches are blocked."""
        dispatcher = Dispatcher()
        # Batch needs clean water (infant formula)
        batch = Batch(
            item="infant_formula",
            quantity=50,
            expiry_minutes=90,
            tags=["infant_formula"],
            donor_lat=28.6,
            donor_lon=77.2,
        )
        # Shelter has NO clean water
        unsafe_shelter = Shelter(
            id="s_unsafe",
            name="Unsafe Shelter",
            capacity=100,
            capabilities=["refrigeration"],  # missing clean_water
            lat=28.61,
            lon=77.21,
        )
        rider = Rider(id="r1", capacity=50, lat=28.6, lon=77.2)

        dispatcher.register_shelter(unsafe_shelter)
        dispatcher.register_rider(rider)

        plan, lots = dispatcher.post_batch(batch)
        # Plan must block the unsafe shelter -> 0 rescuable units
        assert plan.rescuable_units == 0
        assert len(lots) == 0

    def test_invariant_no_negative_slack_assignment(self):
        """Invariant 4: Deliveries with negative slack are strictly rejected."""
        dispatcher = Dispatcher()
        # Very tight expiry: 5 minutes
        batch = Batch(
            item="hot_soup",
            quantity=30,
            expiry_minutes=5,
            donor_lat=28.6,
            donor_lon=77.2,
        )
        # Shelter is 50 km away (travel time > 100 minutes at 15 km/h)
        distant_shelter = Shelter(id="s_far", capacity=50, lat=29.0, lon=77.5)
        distant_rider = Rider(id="r_far", capacity=30, lat=28.0, lon=77.0)

        dispatcher.register_shelter(distant_shelter)
        dispatcher.register_rider(distant_rider)

        # Plan generator excludes negative slack
        plan, lots = dispatcher.post_batch(batch)
        assert len(lots) == 0
        assert plan.rescuable_units == 0

        # Direct slack calculation confirms negative value
        sl = calculate_chunk_slack(batch, distant_rider, distant_shelter)
        assert sl < 0
