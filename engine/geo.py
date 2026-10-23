"""
Geographic utilities.

- Haversine distance between two lat/lon points.
- Travel time estimation: distance ÷ speed × traffic multiplier.
"""

from __future__ import annotations

import math

# Earth's mean radius in kilometres
EARTH_RADIUS_KM = 6371.0

# Default rider speed in km/h (bicycle / scooter in urban area)
DEFAULT_SPEED_KMH = 15.0

# Default traffic multiplier (1.0 = normal, 2.0 = double travel time, etc.)
DEFAULT_TRAFFIC_MULTIPLIER = 1.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Return the great-circle distance in **kilometres** between two points
    specified in decimal degrees.
    """
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


def travel_time_minutes(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    speed_kmh: float = DEFAULT_SPEED_KMH,
    traffic_multiplier: float = DEFAULT_TRAFFIC_MULTIPLIER,
) -> float:
    """
    Estimate travel time in **minutes** between two points.

    travel_time = (distance / speed) × traffic_multiplier × 60

    The traffic multiplier is controlled by the chaos console during demos.
    """
    dist = haversine_km(lat1, lon1, lat2, lon2)
    if speed_kmh <= 0:
        return float("inf")
    hours = dist / speed_kmh
    return hours * 60.0 * traffic_multiplier
