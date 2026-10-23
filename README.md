# Last-Mile Aid Dispatch

> Every night food that could feed hundreds is thrown away — not because nobody wants to help, but because coordination fails.

**Last-Mile Aid Dispatch** is a real-time dispatch engine that coordinates the rescue of perishable food donations, routing volunteer riders from donors to shelters before time runs out.

## Architecture

```
last-mile-aid/
  engine/            # the brain: no web code
    models.py        # Batch, Rider, Shelter, Lot, Lease
    slack.py         # slack, point of no return
    rules.py         # safety and compatibility rules
    dispatcher.py    # chunking, assignment, leases
    reroute.py       # fallback chains, silent-rider alerts
    event_log.py     # append-only event diary
    geo.py           # Haversine distance, travel time
  api/               # FastAPI routes and WebSocket
  web/               # donor.html, rider.html, shelter.html, ops.html
  sim/               # simulated clock, bot riders, chaos events
  tests/             # one file per scenario
```

## Tech Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Frontend | Plain HTML, CSS, JS | No build tooling; four simple pages |
| Map | Leaflet.js + OpenStreetMap | Free, no API key |
| Realtime | WebSockets | Dashboard updates instantly |
| Backend | FastAPI | Built-in WebSockets, auto docs at `/docs` |
| Database | SQLite | Zero setup; holds the event log |
| Tests | pytest | One file per scenario |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn api.main:app --reload

# Run tests
pytest
```

## Demo Scenarios

| # | Scenario | What happens |
|---|----------|-------------|
| 1 | Banquet Rush | 180 meals, 75 min — chunking, surge broadcast |
| 2 | Ghost Rider | Rider goes silent before pickup — lease expires, standby takes over |
| 3 | Pulled Rug | Shelter fills mid-transit — instant reroute |
| 4 | Unsafe Match | Infant formula → no clean water — hard-blocked |
| 5 | Vanishing Rider | Silent after pickup — Yellow/Orange/Red alerts, honest loss |

## License

MIT
