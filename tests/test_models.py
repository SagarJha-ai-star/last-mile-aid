"""Unit tests for engine/models.py — domain model construction and defaults."""

from engine.models import (
    Batch, Lot, Rider, Shelter, Lease,
    LotState, RiderStatus, LeaseState,
)


class TestBatch:
    def test_defaults(self):
        b = Batch(item="rice", quantity=100, expiry_minutes=60)
        assert b.item == "rice"
        assert b.quantity == 100
        assert b.expiry_minutes == 60
        assert b.id  # auto-generated UUID
        assert b.tags == []

    def test_tags(self):
        b = Batch(item="formula", quantity=20, tags=["infant_formula"])
        assert "infant_formula" in b.tags

    def test_unique_ids(self):
        b1 = Batch()
        b2 = Batch()
        assert b1.id != b2.id


class TestLot:
    def test_defaults(self):
        lot = Lot(batch_id="b1", quantity=40)
        assert lot.state == LotState.PENDING
        assert lot.assigned_rider_id is None
        assert lot.target_shelter_id is None

    def test_state_values(self):
        assert LotState.PENDING.value == "pending"
        assert LotState.DELIVERED.value == "delivered"
        assert LotState.LOST.value == "lost"


class TestRider:
    def test_defaults(self):
        r = Rider(name="Aarav", capacity=40)
        assert r.name == "Aarav"
        assert r.capacity == 40
        assert r.status == RiderStatus.AVAILABLE
        assert r.last_ping is None

    def test_status_values(self):
        assert RiderStatus.SILENT_YELLOW.value == "silent_yellow"
        assert RiderStatus.SILENT_RED.value == "silent_red"


class TestShelter:
    def test_defaults(self):
        s = Shelter(name="Hope Center", capacity=50, capabilities=["clean_water"])
        assert s.is_open is True
        assert "clean_water" in s.capabilities

    def test_needs(self):
        s = Shelter(needs=["rice", "blankets"])
        assert len(s.needs) == 2


class TestLease:
    def test_defaults(self):
        le = Lease(lot_id="l1", rider_id="r1", ttl_seconds=300)
        assert le.state == LeaseState.ACTIVE
        assert le.ttl_seconds == 300
        assert le.started_at is None

    def test_state_values(self):
        assert LeaseState.EXPIRED.value == "expired"
        assert LeaseState.RELEASED.value == "released"
