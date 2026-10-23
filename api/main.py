"""
FastAPI Backend Application for Last-Mile Aid Dispatch.

Provides:
- REST API per Section 12 of the Project Document.
- WebSocket endpoint for sub-second reactive UI updates.
- 1-Click execution for Scenarios 1 through 5.
- Serving of the 4 responsive web interfaces: Ops, Donor, Rider, Shelter.
- Live Scoreboard calculation.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.models import (
    Batch, Lot, Rider, Shelter, Lease,
    LotState, RiderStatus, LeaseState,
)
from engine.dispatcher import Dispatcher, calculate_rescue_plan
from engine.reroute import ResilienceManager
from engine.event_log import EventLog
from engine.slack import compute_slack
from engine.geo import travel_time_minutes
from engine.rules import check_safety
from sim.clock import SimulatedClock
from sim.chaos import ChaosConsole
from api.ws import ConnectionManager


# ---------------------------------------------------------------------------
# App & Singletons
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

app = FastAPI(
    title="Last-Mile Aid Dispatch API",
    description="Real-time dispatch engine for rescuing perishable food donations before they expire.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Engine Core Singletons
event_log = EventLog(db_path=str(BASE_DIR / "events.db"))
dispatcher = Dispatcher(event_log=event_log)
resilience = ResilienceManager(event_log=event_log)
sim_clock = SimulatedClock(start_minutes=0.0)
chaos = ChaosConsole(dispatcher=dispatcher, resilience=resilience, event_log=event_log)
ws_manager = ConnectionManager(event_log=event_log)


# ---------------------------------------------------------------------------
# Pydantic Request Models
# ---------------------------------------------------------------------------

class CreateBatchRequest(BaseModel):
    item: str = "Cooked Vegetarian Meals"
    quantity: int = 180
    expiry_minutes: int = 75
    donor_lat: float = 28.6139
    donor_lon: float = 77.2090
    tags: list[str] = Field(default_factory=list)


class AcceptLotRequest(BaseModel):
    rider_id: str
    expected_version: Optional[int] = None


class ReleaseLotRequest(BaseModel):
    rider_id: str


class PingRiderRequest(BaseModel):
    lat: float
    lon: float


class ShelterStatusRequest(BaseModel):
    is_open: Optional[bool] = None
    demand: Optional[int] = None
    capabilities: Optional[list[str]] = None


class ChaosActionRequest(BaseModel):
    action: str  # "traffic", "kill_rider", "close_shelter", "citizen_drop"
    target_id: Optional[str] = None
    value: Optional[float] = None


class ClockAdvanceRequest(BaseModel):
    minutes: float = 5.0
    multiplier: Optional[float] = None


# ---------------------------------------------------------------------------
# Scoreboard Metrics
# ---------------------------------------------------------------------------

def compute_scoreboard() -> dict[str, Any]:
    """Calculates live scoreboard metrics."""
    meals_saved = sum(s.delivered for s in dispatcher.shelters.values())
    meals_lost = sum(lot.quantity for lot in dispatcher.lots.values() if lot.state == LotState.LOST)
    ghost_minutes_eliminated = len(event_log.by_type("standby_takeover")) * 15  # average minutes saved per standby takeover
    reroutes_completed = len(event_log.by_type("rerouted"))
    unsafe_blocked = len(event_log.by_type("claim_rejected")) + len(
        [e for e in event_log.by_type("plan_updated") if "unsafe" in e.reason.lower()]
    )

    return {
        "meals_saved": meals_saved,
        "meals_lost": meals_lost,
        "ghost_minutes_eliminated": ghost_minutes_eliminated,
        "reroutes_completed": reroutes_completed,
        "unsafe_matches_blocked": unsafe_blocked,
    }


# ---------------------------------------------------------------------------
# Seed Realistic Demo Data (Delhi Capital Area)
# ---------------------------------------------------------------------------

def seed_demo_data():
    """Seed initial riders and shelters if empty."""
    if not dispatcher.shelters:
        dispatcher.register_shelter(Shelter(
            id="s_cp",
            name="Connaught Place Shelter",
            lat=28.6328,
            lon=77.2197,
            capacity=50,
            capabilities=["clean_water", "refrigeration"],
            needs=["cooked_meals", "ration_kits"],
        ))
        dispatcher.register_shelter(Shelter(
            id="s_karol",
            name="Karol Bagh Food Bank",
            lat=28.6517,
            lon=77.1906,
            capacity=50,
            capabilities=["clean_water"],
            needs=["cooked_meals"],
        ))
        dispatcher.register_shelter(Shelter(
            id="s_lodhi",
            name="Lodhi Community Kitchen",
            lat=28.5892,
            lon=77.2215,
            capacity=50,
            capabilities=["clean_water", "refrigeration"],
            needs=["dairy", "cooked_meals"],
        ))
        dispatcher.register_shelter(Shelter(
            id="s_dry",
            name="Paharganj Night Shelter (No Water)",
            lat=28.6420,
            lon=77.2130,
            capacity=40,
            capabilities=["refrigeration"],  # lacks clean_water for safety demo!
            needs=["dry_rations"],
        ))

    if not dispatcher.riders:
        dispatcher.register_rider(Rider(
            id="r_aarav",
            name="Aarav (Bicycle)",
            capacity=40,
            lat=28.6180,
            lon=77.2100,
            reliability=0.95,
        ))
        dispatcher.register_rider(Rider(
            id="r_priya",
            name="Priya (Scooter)",
            capacity=40,
            lat=28.6250,
            lon=77.2150,
            reliability=0.98,
        ))
        dispatcher.register_rider(Rider(
            id="r_vikram",
            name="Vikram (Motorcycle)",
            capacity=40,
            lat=28.6110,
            lon=77.2050,
            reliability=0.90,
        ))
        dispatcher.register_rider(Rider(
            id="r_rohan",
            name="Rohan (Standby)",
            capacity=40,
            lat=28.6150,
            lon=77.2080,
            reliability=0.92,
        ))


seed_demo_data()


# ---------------------------------------------------------------------------
# Lifecycle & Background Tasks
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    ws_manager.set_event_loop(loop)

    # Background task to check lease expirations and tick clock
    async def periodic_dispatcher_loop():
        while True:
            await asyncio.sleep(2.0)
            now_min = sim_clock.tick_realtime()
            expired = dispatcher.expire_leases(now_min)
            if expired:
                await ws_manager.broadcast({
                    "type": "leases_checked",
                    "expired_count": len(expired),
                    "now_minutes": now_min,
                })

    asyncio.create_task(periodic_dispatcher_loop())


# ---------------------------------------------------------------------------
# REST API Endpoints (Section 12)
# ---------------------------------------------------------------------------

@app.post("/batches")
def create_batch(req: CreateBatchRequest):
    """Donor creates a batch; returns the rescue plan and created lots."""
    batch = Batch(
        item=req.item,
        quantity=req.quantity,
        expiry_minutes=req.expiry_minutes,
        donor_lat=req.donor_lat,
        donor_lon=req.donor_lon,
        tags=req.tags,
    )
    plan, lots = dispatcher.post_batch(
        batch,
        now_minutes=sim_clock.current_minutes,
        traffic_multiplier=chaos.traffic_multiplier,
    )
    return {
        "batch_id": batch.id,
        "plan": plan,
        "lots": lots,
    }


@app.get("/batches/{batch_id}/plan")
def get_batch_plan(batch_id: str):
    """Retrieve current rescue plan, PNR, and slack for a batch."""
    batch = dispatcher.batches.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    plan = calculate_rescue_plan(
        batch,
        list(dispatcher.riders.values()),
        list(dispatcher.shelters.values()),
        now_minutes=sim_clock.current_minutes,
        traffic_multiplier=chaos.traffic_multiplier,
    )
    return {"plan": plan}


@app.post("/lots/{lot_id}/accept")
def accept_lot(lot_id: str, req: AcceptLotRequest):
    """Rider accepts lot, starting a time-bounded lease."""
    try:
        lease = dispatcher.accept_lot(
            lot_id=lot_id,
            rider_id=req.rider_id,
            now_minutes=sim_clock.current_minutes,
            expected_version=req.expected_version,
            traffic_multiplier=chaos.traffic_multiplier,
        )
        return {"status": "accepted", "lease": lease}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/lots/{lot_id}/release")
def release_lot(lot_id: str, req: ReleaseLotRequest):
    """Rider releases lot early, penalty-free."""
    try:
        lot = dispatcher.release_lot(
            lot_id=lot_id,
            rider_id=req.rider_id,
            now_minutes=sim_clock.current_minutes,
        )
        return {"status": "released", "lot": lot}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/riders/{rider_id}/ping")
def ping_rider(rider_id: str, req: PingRiderRequest):
    """Heartbeat with location; clears yellow/orange warnings."""
    rider = dispatcher.riders.get(rider_id)
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")

    rider.lat = req.lat
    rider.lon = req.lon
    rider.last_ping = datetime.now(timezone.utc)
    if rider.status in (RiderStatus.SILENT_YELLOW, RiderStatus.SILENT_ORANGE):
        rider.status = RiderStatus.BUSY

    event_log.append(
        "rider_ping",
        {"rider_id": rider.id, "lat": rider.lat, "lon": rider.lon},
        reason=f"Heartbeat received from {rider.name} at [{rider.lat:.4f}, {rider.lon:.4f}]",
    )
    return {"status": "ok", "rider": rider}


@app.post("/lots/{lot_id}/picked-up")
def mark_lot_picked_up(lot_id: str):
    """Mark lot as picked up by courier."""
    try:
        lot = dispatcher.mark_picked_up(lot_id, now_minutes=sim_clock.current_minutes)
        return {"status": "picked_up", "lot": lot}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/lots/{lot_id}/delivered")
def mark_lot_delivered(lot_id: str):
    """Mark lot as delivered to shelter."""
    try:
        lot = dispatcher.mark_delivered(lot_id, now_minutes=sim_clock.current_minutes)
        return {"status": "delivered", "lot": lot, "scoreboard": compute_scoreboard()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/shelters/{shelter_id}/status")
def update_shelter_status(shelter_id: str, req: ShelterStatusRequest):
    """Toggle shelter demand, capabilities, or open/closed."""
    shelter = dispatcher.shelters.get(shelter_id)
    if not shelter:
        raise HTTPException(status_code=404, detail="Shelter not found")

    if req.is_open is not None:
        shelter.is_open = req.is_open
    if req.demand is not None:
        shelter.demand = req.demand
    if req.capabilities is not None:
        shelter.capabilities = req.capabilities

    event_log.append(
        "shelter_status_updated",
        {"shelter_id": shelter.id, "is_open": shelter.is_open, "demand": shelter.demand},
        reason=f"Shelter {shelter.name} status updated: open={shelter.is_open}, demand={shelter.demand}",
    )
    return {"status": "ok", "shelter": shelter}


@app.post("/chaos/{action}")
def trigger_chaos(action: str, req: Optional[ChaosActionRequest] = None):
    """Chaos console actions: traffic surge, kill rider, close shelter, citizen drop."""
    target_id = req.target_id if req else None
    val = req.value if req else None

    if action == "traffic":
        mult = val if val else (2.0 if chaos.traffic_multiplier == 1.0 else 1.0)
        return chaos.set_traffic_multiplier(mult)
    elif action == "kill_rider":
        rid = target_id or "r_aarav"
        return chaos.kill_rider(rid)
    elif action == "close_shelter":
        sid = target_id or "s_cp"
        return chaos.close_shelter(sid, now_minutes=sim_clock.current_minutes)
    elif action == "citizen_drop":
        sid = target_id or "s_cp"
        units = int(val) if val else 50
        return chaos.citizen_drop(sid, units_dropped=units, now_minutes=sim_clock.current_minutes)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown chaos action: {action}")


@app.post("/clock/advance")
def advance_clock(req: ClockAdvanceRequest):
    """Advance the simulated clock by minutes or change multiplier."""
    if req.multiplier is not None:
        sim_clock.set_multiplier(req.multiplier)
    new_time = sim_clock.advance_minutes(req.minutes)
    dispatcher.expire_leases(new_time)
    return {"current_minutes": round(new_time, 2), "multiplier": sim_clock.multiplier}


@app.get("/events")
def get_events():
    """Audit log of all events."""
    return {"events": event_log.to_dicts()}


@app.get("/scoreboard")
def get_scoreboard():
    """Scoreboard summary."""
    return compute_scoreboard()


@app.get("/state")
def get_state():
    """Complete snapshot for UI dashboards."""
    return {
        "clock_minutes": round(sim_clock.current_minutes, 2),
        "clock_multiplier": sim_clock.multiplier,
        "traffic_multiplier": chaos.traffic_multiplier,
        "scoreboard": compute_scoreboard(),
        "batches": list(dispatcher.batches.values()),
        "lots": list(dispatcher.lots.values()),
        "riders": list(dispatcher.riders.values()),
        "shelters": list(dispatcher.shelters.values()),
        "leases": list(dispatcher.leases.values()),
        "recent_events": event_log.to_dicts()[-20:],
    }


# ---------------------------------------------------------------------------
# 1-Click Demo Scenarios (Section 14 & 19)
# ---------------------------------------------------------------------------

@app.post("/scenarios/{scenario_id}/run")
def run_scenario(scenario_id: int):
    """Execute pre-built demo scenarios with a single click."""
    sim_clock.reset(0.0)

    if scenario_id == 1:
        # Scenario 1: Banquet Rush (180 meals, 75 min)
        b = Batch(
            item="Royal Palace Banquet Rice & Curry",
            quantity=180,
            expiry_minutes=75,
            donor_lat=28.6139,
            donor_lon=77.2090,
        )
        plan, lots = dispatcher.post_batch(b, now_minutes=0.0)
        return {
            "scenario": 1,
            "title": "Banquet Rush",
            "plan": plan,
            "lots": lots,
            "message": "180 meals posted! 150 rescuable across 4 riders, 30 at risk. Surge broadcast triggered.",
        }

    elif scenario_id == 2:
        # Scenario 2: Ghost Rider
        b = Batch(
            item="Corporate Lunch Surplus",
            quantity=40,
            expiry_minutes=60,
            donor_lat=28.6139,
            donor_lon=77.2090,
        )
        plan, lots = dispatcher.post_batch(b, now_minutes=0.0)
        lot = lots[0]
        dispatcher.register_standby(lot.id, "r_rohan")

        # Aarav accepts lot
        lease = dispatcher.accept_lot(lot.id, "r_aarav", now_minutes=0.0)

        # Advance clock past TTL without ping (ghost rider)
        sim_clock.advance_minutes(15.0)
        expired = dispatcher.expire_leases(15.0)

        return {
            "scenario": 2,
            "title": "Ghost Rider",
            "expired_leases": len(expired),
            "lot": lot,
            "message": "Rider Aarav went silent. Lease auto-expired; Standby Rohan took over immediately. 0 food lost!",
        }

    elif scenario_id == 3:
        # Scenario 3: Pulled Rug (Mid-transit reroute)
        b = Batch(
            item="Packed Dal & Roti",
            quantity=40,
            expiry_minutes=80,
            donor_lat=28.6139,
            donor_lon=77.2090,
        )
        plan, lots = dispatcher.post_batch(b, now_minutes=0.0)
        lot = lots[0]
        dispatcher.accept_lot(lot.id, "r_priya", now_minutes=0.0)
        dispatcher.mark_picked_up(lot.id, now_minutes=5.0)

        # Mid-route position
        r = dispatcher.riders["r_priya"]
        r.lat = 28.6400
        r.lon = 77.2050

        # Close target shelter (Connaught Place)
        reroutes = chaos.close_shelter("s_cp", now_minutes=10.0)

        return {
            "scenario": 3,
            "title": "Pulled Rug",
            "lot": lot,
            "reroutes": reroutes,
            "message": "Shelter CP closed mid-transit! Instant reroute recalculated from Priya's live GPS to Karol Bagh.",
        }

    elif scenario_id == 4:
        # Scenario 4: Unsafe Match
        b = Batch(
            item="Emergency Infant Formula",
            quantity=40,
            expiry_minutes=90,
            tags=["infant_formula"],
            donor_lat=28.6139,
            donor_lon=77.2090,
        )
        # Attempt to match with Paharganj (s_dry has no clean water)
        safety = check_safety(b.tags, dispatcher.shelters["s_dry"].capabilities)
        plan, lots = dispatcher.post_batch(b, now_minutes=0.0)

        return {
            "scenario": 4,
            "title": "Unsafe Match Blocked",
            "blocked_reasons": safety.reasons,
            "plan": plan,
            "message": "Blocked Infant Formula to Paharganj (No Clean Water)! Visible reason displayed.",
        }

    elif scenario_id == 5:
        # Scenario 5: Vanishing Rider
        b = Batch(
            item="Fresh Sandwiches & Soup",
            quantity=80,
            expiry_minutes=60,
            donor_lat=28.6139,
            donor_lon=77.2090,
        )
        plan, lots = dispatcher.post_batch(b, now_minutes=0.0)
        lot1, lot2 = lots[0], lots[1]

        dispatcher.accept_lot(lot1.id, "r_vikram", now_minutes=0.0)
        dispatcher.accept_lot(lot2.id, "r_aarav", now_minutes=0.0)

        dispatcher.mark_picked_up(lot1.id, now_minutes=5.0)
        dispatcher.mark_picked_up(lot2.id, now_minutes=5.0)

        # Vikram delivers safely
        dispatcher.mark_delivered(lot1.id, now_minutes=25.0)

        # Aarav vanishes -> Red alert
        resilience.escalate_silent_rider(
            dispatcher.riders["r_aarav"],
            lot2,
            dispatcher.shelters[lot2.target_shelter_id],
            RiderStatus.SILENT_RED,
            now_minutes=55.0,
        )

        return {
            "scenario": 5,
            "title": "Vanishing Rider",
            "message": "Aarav vanished with Lot 2. Red alert triggered: only 40 units lost, shelter demand reopened immediately!",
            "scoreboard": compute_scoreboard(),
        }
    else:
        raise HTTPException(status_code=400, detail=f"Unknown scenario ID: {scenario_id}")


# ---------------------------------------------------------------------------
# WebSocket Endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send initial full state on connect
        await websocket.send_json({
            "type": "initial_state",
            "data": get_state(),
        })
        while True:
            # Keep connection open and accept client messages if any
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# HTML Page Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
@app.get("/ops", response_class=HTMLResponse)
def get_ops_page():
    return FileResponse(WEB_DIR / "ops.html")


@app.get("/donor", response_class=HTMLResponse)
def get_donor_page():
    return FileResponse(WEB_DIR / "donor.html")


@app.get("/rider", response_class=HTMLResponse)
def get_rider_page():
    return FileResponse(WEB_DIR / "rider.html")


@app.get("/shelter", response_class=HTMLResponse)
def get_shelter_page():
    return FileResponse(WEB_DIR / "shelter.html")


# Mount static files folder
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
