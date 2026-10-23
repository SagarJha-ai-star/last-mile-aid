"""
Dispatcher: chunking, assignment, and lease management.

Responsibilities:
- Split a Batch into rider-sized Lots.
- Assign Lots to Riders targeting Shelters (greedy matching).
- Manage Leases (create, expire, release).
- Surge-broadcast when riders are scarce.
"""
