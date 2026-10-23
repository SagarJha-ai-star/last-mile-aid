"""Unit tests for engine/event_log.py — append-only event log."""

import json
import os
import tempfile
from engine.event_log import Event, EventLog


class TestEvent:
    def test_auto_timestamp(self):
        e = Event(type="test")
        assert e.timestamp is not None

    def test_auto_id(self):
        e1 = Event(type="a")
        e2 = Event(type="b")
        assert e1.id != e2.id


class TestEventLogInMemory:
    def test_append_and_all(self):
        log = EventLog()
        log.append("batch_created", {"batch_id": "b1"})
        log.append("lot_assigned", {"lot_id": "l1"})
        assert len(log.all()) == 2

    def test_by_type(self):
        log = EventLog()
        log.append("batch_created")
        log.append("lot_assigned")
        log.append("batch_created")
        assert len(log.by_type("batch_created")) == 2
        assert len(log.by_type("lot_assigned")) == 1

    def test_last(self):
        log = EventLog()
        log.append("a")
        log.append("b")
        log.append("c")
        last_two = log.last(2)
        assert [e.type for e in last_two] == ["b", "c"]

    def test_clear(self):
        log = EventLog()
        log.append("x")
        log.clear()
        assert len(log.all()) == 0

    def test_reason_stored(self):
        log = EventLog()
        ev = log.append("blocked", reason="No clean water")
        assert ev.reason == "No clean water"

    def test_subscriber_called(self):
        log = EventLog()
        received = []
        log.subscribe(lambda e: received.append(e))
        log.append("ping")
        assert len(received) == 1
        assert received[0].type == "ping"

    def test_unsubscribe(self):
        log = EventLog()
        received = []
        cb = lambda e: received.append(e)
        log.subscribe(cb)
        log.append("a")
        log.unsubscribe(cb)
        log.append("b")
        assert len(received) == 1

    def test_to_dicts(self):
        log = EventLog()
        log.append("test", {"key": "val"})
        dicts = log.to_dicts()
        assert len(dicts) == 1
        assert dicts[0]["type"] == "test"
        assert dicts[0]["payload"]["key"] == "val"


class TestEventLogSQLite:
    def test_persist_and_reload(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Write events
            log1 = EventLog(db_path=db_path)
            log1.append("batch_created", {"batch_id": "b1"})
            log1.append("lot_assigned", {"lot_id": "l1"}, reason="closest rider")
            log1.close()

            # Reload into a fresh EventLog
            log2 = EventLog(db_path=db_path)
            log2.load_from_db()
            assert len(log2.all()) == 2
            assert log2.all()[0].type == "batch_created"
            assert log2.all()[1].reason == "closest rider"
            log2.close()
        finally:
            os.unlink(db_path)
