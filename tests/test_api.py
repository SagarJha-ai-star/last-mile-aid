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
