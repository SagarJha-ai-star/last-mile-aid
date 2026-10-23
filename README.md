# 🚀 Last-Mile Aid Dispatch

> *"Every night food that could feed hundreds is thrown away — not because nobody wants to help, but because coordination fails. Watch what happens when we break it."*

**Last-Mile Aid Dispatch** is a resilient, real-time spatial dispatch engine designed to coordinate the rescue of perishable food donations. It dynamically routes volunteer couriers from donors to recipient shelters before perishability deadlines expire.

---

## 🏛️ Architecture

```
last-mile-aid/
  engine/            # The brain: pure business logic & zero web dependencies
    models.py        # Domain entities: Batch, Lot, Rider, Shelter, Lease
    slack.py         # Spare time & Point of No Return (PNR) math
    rules.py         # Declarative safety rule engine (hard-blocking)
    dispatcher.py    # Chunking, greedy assignment, adaptive leases
    reroute.py       # Live-GPS mid-journey rerouting & silent-rider escalation
    event_log.py     # Append-only event store (in-memory + SQLite)
    geo.py           # Haversine distance & traffic-adjusted ETA math
  api/               # FastAPI REST endpoints & WebSocket event broadcaster
    main.py          # REST routes & 1-click scenario runner
    ws.py            # Reactive WebSocket connection manager
  web/               # Four glassmorphic responsive frontends (Vanilla CSS + Leaflet.js)
    ops.html         # Ops Command Center with live map & "Why?" decision panel
    donor.html       # Donor portal: posting batches & live PNR countdown
    rider.html       # Mobile courier terminal with visible lease countdown
    shelter.html     # Shelter intake console with safety capability flags
  sim/               # Deterministic simulation & failure injection
    clock.py         # Accelerated simulated clock (1x, 5x, 10x, 60x)
    chaos.py         # Chaos Console: traffic surges, dropped pings, shelter closures
    bot_riders.py    # Autonomous bot couriers moving on map
  tests/             # Comprehensive scenario & invariant test suite
    test_scenario_1.py  # Banquet Rush
    test_scenario_2.py  # Ghost Rider
    test_scenario_3.py  # Pulled Rug
    test_scenario_4.py  # Unsafe Match
    test_scenario_5.py  # Vanishing Rider
    test_invariants.py  # 4 core system invariants
    test_replay.py      # Deterministic event log replay
```

---

## ⚡ Tech Stack & Design Choices

| Layer | Choice | Rationale |
|---|---|---|
| **Core Logic** | Python 3.10+ | Clean separation of engine from web layer; port of C++ reference logic |
| **API Framework** | FastAPI + Uvicorn | Built-in async WebSockets, sub-second latency, auto-documentation |
| **Persistence** | Append-Only SQLite | Zero configuration; deterministic event log replay and audit trail |
| **Spatial Mapping** | Leaflet.js + CARTO Dark | Free, open-source, no external API keys required |
| **UI Aesthetics** | Vanilla Glassmorphic CSS | Modern Outfit / JetBrains Mono typography, glowing dark theme |

---

## 🚀 Quick Start

### 1. Setup Virtual Environment & Dependencies
```bash
# Activate virtual environment
.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### 2. Launch the Application
```bash
uvicorn api.main:app --reload --port 8000
```
Open **[http://localhost:8000/ops](http://localhost:8000/ops)** in your browser to launch the **Ops Command Center**.

### 3. Run the Full Test Suite
```bash
pytest tests/ -v
```
*70 automated tests covering all scenarios, invariants, models, and replay determinism.*

---

## 🎬 5-Step Demo Script (Walkthrough)

1. **Banquet Rush (Scenario 1)**:
   - Donor posts 180 meals expiring in 75 minutes.
   - Screen instantly shows **150 rescuable across 4 riders; 30 at risk**.
   - Surge broadcast is triggered to alert nearby couriers with PNR countdown urgency.
2. **Ghost Rider (Scenario 2)**:
   - Courier accepts a chunk; adaptive lease starts with TTL derived from slack.
   - Courier goes silent before pickup; lease auto-expires and a standby courier takes over immediately.
   - **0 food lost.**
3. **Pulled Rug (Scenario 3)**:
   - Shelter A closes while a courier is mid-transit with food in hand.
   - Instant reroute calculates new destination from the courier's **live GPS location** to Shelter B.
   - The **"Why?" Decision Panel** explains the change in plain English.
4. **Unsafe Match (Scenario 4)**:
   - Donor attempts to send emergency infant formula to a shelter with no clean water supply.
   - Safety rule engine hard-blocks the match and displays visible rationale.
5. **Vanishing Rider (Scenario 5)**:
   - Courier vanishes with food after pickup.
   - Escalates through **🟡 Yellow (suspect)** → **🟠 Orange (at risk)** → **🔴 Red (declared lost)**.
   - **Damage containment**: Only that 1 chunk (40 meals) is written off; shelter demand is immediately reopened so other aid can arrive.
   - Concludes on the live **Scoreboard**: Meals saved, meals lost, ghost-minutes eliminated, and why.

---

## 🛡️ Core System Invariants

The dispatch engine guarantees 4 strict invariants tested continuously:
- **No lot claimed twice**: Versioned compare-and-set claims prevent race conditions.
- **Sum of lots never exceeds batch**: Total distributed aid never exceeds donated quantity.
- **No unsafe match accepted**: Pluggable safety rules hard-block incompatible deliveries.
- **No assignment with negative slack**: Deliveries that cannot physically arrive before expiry are rejected.
- **Deterministic replay**: Folding over the immutable event stream twice produces 100% identical system state.

---

## 📜 License
MIT
