"""
API integration test — verifies FastAPI routes, scenarios, and state endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_api_state(client):
    res = client.get("/state")
    assert res.status_code == 200
    data = res.json()
    assert "scoreboard" in data
    assert "riders" in data
    assert "shelters" in data
    assert len(data["shelters"]) >= 3


def test_api_post_batch(client):
    payload = {
        "item": "Test Rice",
        "quantity": 100,
        "expiry_minutes": 60,
        "donor_lat": 28.6139,
        "donor_lon": 77.2090,
    }
    res = client.post("/batches", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "plan" in data
    assert data["plan"]["rescuable_units"] > 0


def test_api_1click_scenario_runner(client):
    # Test Scenario 1 (Banquet rush) 1-click execution
    res = client.post("/scenarios/1/run")
    assert res.status_code == 200
    data = res.json()
    assert data["scenario"] == 1
    assert "Banquet Rush" in data["title"]


def test_api_autopilot_endpoints(client):
    # Status check
    res = client.get("/autopilot/status")
    assert res.status_code == 200
    assert "enabled" in res.json()

    # Speed check
    res = client.post("/autopilot/speed", json={"multiplier": 5.0})
    assert res.status_code == 200
    assert res.json()["speed"] == 5.0

    # Toggle
    res = client.post("/autopilot/toggle")
    assert res.status_code == 200
    assert res.json()["enabled"] in [True, False]


def test_api_crypto_custody_seal(client):
    # Run scenario 1 to ensure lots exist
    client.post("/scenarios/1/run")
    state = client.get("/state").json()
    lots = state["lots"]
    assert len(lots) > 0
    lot_id = lots[0]["id"]

    # Rider accepts and picks up the lot
    client.post(f"/lots/{lot_id}/accept", json={"rider_id": "r_aarav"})
    client.post(f"/lots/{lot_id}/picked-up")

    # Generate custody seal
    res = client.post(f"/lots/{lot_id}/seal", json={"temp_celsius": 3.8})
    assert res.status_code == 200
    seal_data = res.json()
    assert "seal" in seal_data
    token = seal_data["seal"]["token"]

    # Verify custody seal
    res_verify = client.post(f"/lots/{lot_id}/verify-seal", json={"token": token})
    assert res_verify.status_code == 200
    assert res_verify.json()["status"] == "verified"
    assert "scoreboard" in res_verify.json()
    assert "co2e_avoided_kg" in res_verify.json()["scoreboard"]
    assert res_verify.json()["scoreboard"]["custody_transfers"] >= 1

