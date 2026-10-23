"""
Chaos Console: Inject failures and disruptions to demonstrate system resilience.

Actions:
- traffic_surge: Double or triple travel times.
- kill_rider: Cut off rider pings to trigger ghost/silent alerts.
- close_shelter: Close a shelter mid-journey ('pulled rug').
- citizen_drop: Citizen delivers food unannounced, filling shelter capacity.
- slash_expiry: Shorten a batch's expiry deadline under crisis conditions.
"""

from __future__ import annotations

from typing import Any, Optional
from engine.models import Rider, Shelter, Batch, RiderStatus
from engine.dispatcher import Dispatcher
from engine.reroute import ResilienceManager
from engine.event_log import EventLog


class ChaosConsole:
    """
    Manages chaos injection into the active dispatch simulation.
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        resilience: ResilienceManager,
        event_log: EventLog,
    ):
        self.dispatcher = dispatcher
        self.resilience = resilience
        self.event_log = event_log
        self.traffic_multiplier: float = 1.0

    def set_traffic_multiplier(self, multiplier: float) -> dict[str, Any]:
        """Traffic surge: scales all Haversine travel time estimations."""
        old = self.traffic_multiplier
        self.traffic_multiplier = max(multiplier, 0.5)
        self.event_log.append(
            "chaos_traffic_surge",
            {"old_multiplier": old, "new_multiplier": self.traffic_multiplier},
            reason=f"🔥 Chaos: Citywide traffic surge injected ({self.traffic_multiplier:.1f}x multiplier)",
        )
        return {"action": "traffic", "multiplier": self.traffic_multiplier}

    def kill_rider(self, rider_id: str) -> dict[str, Any]:
        """Silence a rider by severing heartbeats."""
        rider = self.dispatcher.riders.get(rider_id)
        if not rider:
            return {"error": f"Rider {rider_id} not found"}

        rider.status = RiderStatus.SILENT_YELLOW
        self.event_log.append(
            "chaos_kill_rider",
            {"rider_id": rider_id},
            reason=f"💀 Chaos: Rider {rider.name} connection killed (missed pings)",
        )
        return {"action": "kill_rider", "rider_id": rider_id, "status": rider.status.value}

    def close_shelter(
        self,
        shelter_id: str,
        now_minutes: float = 0.0,
    ) -> dict[str, Any]:
        """Close shelter mid-transit, triggering instant reroute for inbound riders."""
        shelter = self.dispatcher.shelters.get(shelter_id)
        if not shelter:
            return {"error": f"Shelter {shelter_id} not found"}

        reroutes = self.resilience.handle_shelter_closed_or_full(
            closed_shelter_id=shelter_id,
            lots=self.dispatcher.lots,
            riders=self.dispatcher.riders,
            batches=self.dispatcher.batches,
            shelters=self.dispatcher.shelters,
            now_minutes=now_minutes,
            traffic_multiplier=self.traffic_multiplier,
        )
        return {
            "action": "close_shelter",
            "shelter_id": shelter_id,
            "reroutes_count": len(reroutes),
            "reroutes": [
                {"lot_id": r.lot_id, "to_shelter": r.new_shelter_id, "slack": r.slack_minutes}
                for r in reroutes
            ],
        }

    def citizen_drop(
        self,
        shelter_id: str,
        units_dropped: int = 50,
        now_minutes: float = 0.0,
    ) -> dict[str, Any]:
        """
        A local citizen drops food directly at a shelter, filling its demand unannounced.
        Triggers instant reroute for any inbound aid that was on its way!
        """
        shelter = self.dispatcher.shelters.get(shelter_id)
        if not shelter:
            return {"error": f"Shelter {shelter_id} not found"}

        # Citizen fills demand
        shelter.delivered += units_dropped
        shelter.demand = max(shelter.demand, shelter.delivered)

        self.event_log.append(
            "chaos_citizen_drop",
            {"shelter_id": shelter_id, "units_dropped": units_dropped},
            reason=f"🍲 Chaos: Citizen dropped {units_dropped} unannounced meals at {shelter.name}, filling its capacity!",
        )

        # Inbound aid now exceeds remaining capacity -> reroute excess inbound
        reroutes = self.resilience.handle_shelter_closed_or_full(
            closed_shelter_id=shelter_id,
            lots=self.dispatcher.lots,
            riders=self.dispatcher.riders,
            batches=self.dispatcher.batches,
            shelters=self.dispatcher.shelters,
            now_minutes=now_minutes,
            traffic_multiplier=self.traffic_multiplier,
        )
        return {
            "action": "citizen_drop",
            "shelter_id": shelter_id,
            "units_dropped": units_dropped,
            "reroutes_count": len(reroutes),
        }
