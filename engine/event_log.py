"""
Append-only event log.

Every state change is recorded as an event. The log is used for:
- Real-time WebSocket pushes.
- The "Why?" decision panel.
- Deterministic replay in tests.
- The audit trail (GET /events).
"""
