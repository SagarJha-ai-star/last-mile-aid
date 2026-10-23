"""
Domain models for Last-Mile Aid Dispatch.

Classes:
    Batch  – A donation of perishable food from a donor.
    Lot    – A chunk of a Batch sized for one rider trip.
    Rider  – A volunteer courier.
    Shelter – A receiving site (food bank, shelter, community centre).
    Lease  – A temporary, time-bounded claim of a Lot by a Rider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LotState(Enum):
    """Lifecycle states of a Lot."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    PICKED_UP = "picked_up"
    DELIVERED = "delivered"
    LOST = "lost"


class RiderStatus(Enum):
    """Operational status of a Rider."""
    AVAILABLE = "available"
    BUSY = "busy"
    SILENT_YELLOW = "silent_yellow"
    SILENT_ORANGE = "silent_orange"
    SILENT_RED = "silent_red"
    OFFLINE = "offline"


class LeaseState(Enum):
    """States of a Lease."""
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    RELEASED = "released"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class Batch:
    """A donation posted by a donor."""
    id: str = field(default_factory=lambda: str(uuid4()))
    item: str = ""
    quantity: int = 0
    expiry_minutes: int = 0  # minutes from creation until unsafe
    donor_lat: float = 0.0
    donor_lon: float = 0.0
    created_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)  # e.g. ["infant_formula"]


@dataclass
class Lot:
    """A rider-sized chunk of a Batch."""
    id: str = field(default_factory=lambda: str(uuid4()))
    batch_id: str = ""
    quantity: int = 0
    state: LotState = LotState.PENDING
    assigned_rider_id: Optional[str] = None
    target_shelter_id: Optional[str] = None


@dataclass
class Rider:
    """A volunteer courier."""
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    capacity: int = 0  # max items per trip
    lat: float = 0.0
    lon: float = 0.0
    status: RiderStatus = RiderStatus.AVAILABLE
    last_ping: Optional[datetime] = None


@dataclass
class Shelter:
    """A receiving site."""
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    capacity: int = 0  # max items it can accept right now
    is_open: bool = True
    capabilities: list[str] = field(default_factory=list)  # e.g. ["clean_water", "refrigeration"]
    needs: list[str] = field(default_factory=list)


@dataclass
class Lease:
    """A temporary claim of a Lot by a Rider, with a TTL derived from slack."""
    id: str = field(default_factory=lambda: str(uuid4()))
    lot_id: str = ""
    rider_id: str = ""
    state: LeaseState = LeaseState.ACTIVE
    started_at: Optional[datetime] = None
    ttl_seconds: int = 0  # time-to-live before auto-expire
    expires_at: Optional[datetime] = None
