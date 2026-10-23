"""
Bot Riders: Autonomous simulated couriers that navigate toward donors and shelters.

Allows hackathon judges to watch real-time movement and deliveries on the Leaflet
map without requiring live smartphones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from engine.models import Rider, Lot, LotState, RiderStatus
from engine.geo import haversine_km, DEFAULT_SPEED_KMH
from engine.dispatcher import Dispatcher
from engine.event_log import EventLog


@dataclass
class BotRiderAgent:
    """An autonomous agent controlling a Rider instance."""
    rider_id: str
    target_lot_id: Optional[str] = None
    target_lat: Optional[float] = None
    target_lon: Optional[float] = None
    phase: str = "idle"  # "idle", "to_donor", "to_shelter"
    speed_kmh: float = DEFAULT_SPEED_KMH

    def step(
        self,
        dispatcher: Dispatcher,
        delta_minutes: float,
        event_log: Optional[EventLog] = None,
    ) -> None:
        """Advance bot position toward current waypoint."""
        rider = dispatcher.riders.get(self.rider_id)
        if not rider:
            return

        # If idle, check if any lot is assigned to this rider
        if self.phase == "idle":
            for lot in dispatcher.lots.values():
                if lot.assigned_rider_id == rider.id and lot.state == LotState.ASSIGNED:
                    batch = dispatcher.batches.get(lot.batch_id)
                    if batch:
                        self.target_lot_id = lot.id
                        self.target_lat = batch.donor_lat
                        self.target_lon = batch.donor_lon
                        self.phase = "to_donor"
                        break

        # If en route to shelter, dynamically update target coordinates in case of mid-journey reroute!
        if self.phase == "to_shelter" and self.target_lot_id:
            lot = dispatcher.lots.get(self.target_lot_id)
            if lot:
                shelter = dispatcher.shelters.get(lot.target_shelter_id)
                if shelter and (self.target_lat != shelter.lat or self.target_lon != shelter.lon):
                    self.target_lat = shelter.lat
                    self.target_lon = shelter.lon

        if not self.target_lat or not self.target_lon:
            return

        dist_km = haversine_km(rider.lat, rider.lon, self.target_lat, self.target_lon)
        max_dist_step = (self.speed_kmh / 60.0) * delta_minutes

        if dist_km <= max_dist_step or dist_km < 0.05:
            # Reached target waypoint
            rider.lat = self.target_lat
            rider.lon = self.target_lon

            if self.phase == "to_donor" and self.target_lot_id:
                # Pick up cargo!
                try:
                    dispatcher.mark_picked_up(self.target_lot_id)
                    lot = dispatcher.lots[self.target_lot_id]
                    shelter = dispatcher.shelters.get(lot.target_shelter_id)
                    if shelter:
                        self.target_lat = shelter.lat
                        self.target_lon = shelter.lon
                        self.phase = "to_shelter"
                except ValueError:
                    self.phase = "idle"

            elif self.phase == "to_shelter" and self.target_lot_id:
                # Deliver cargo!
                try:
                    dispatcher.mark_delivered(self.target_lot_id)
                except ValueError:
                    pass
                self.phase = "idle"
                self.target_lot_id = None
                self.target_lat = None
                self.target_lon = None
        else:
            # Interpolate towards waypoint
            ratio = max_dist_step / dist_km
            rider.lat += (self.target_lat - rider.lat) * ratio
            rider.lon += (self.target_lon - rider.lon) * ratio
