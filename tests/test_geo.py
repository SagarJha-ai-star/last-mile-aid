"""Unit tests for engine/geo.py — Haversine distance and travel time."""

import math
import pytest
from engine.geo import haversine_km, travel_time_minutes


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(28.6139, 77.2090, 28.6139, 77.2090) == 0.0

    def test_known_distance_delhi_to_mumbai(self):
        # Delhi (28.6139, 77.2090) → Mumbai (19.0760, 72.8777)
        dist = haversine_km(28.6139, 77.2090, 19.0760, 72.8777)
        # Known great-circle distance ≈ 1,148 km
        assert 1100 < dist < 1200

    def test_short_distance(self):
        # Two points ~1 km apart in central Delhi
        dist = haversine_km(28.6139, 77.2090, 28.6230, 77.2090)
        assert 0.5 < dist < 2.0

    def test_symmetry(self):
        d1 = haversine_km(28.6139, 77.2090, 19.0760, 72.8777)
        d2 = haversine_km(19.0760, 72.8777, 28.6139, 77.2090)
        assert abs(d1 - d2) < 1e-9


class TestTravelTime:
    def test_zero_distance(self):
        t = travel_time_minutes(28.6, 77.2, 28.6, 77.2)
        assert t == 0.0

    def test_normal_traffic(self):
        # ~1 km at 15 km/h → ~4 minutes
        t = travel_time_minutes(28.6139, 77.2090, 28.6230, 77.2090)
        assert 0 < t < 10

    def test_traffic_multiplier_doubles_time(self):
        t_normal = travel_time_minutes(28.6139, 77.2090, 28.6230, 77.2090,
                                       traffic_multiplier=1.0)
        t_heavy = travel_time_minutes(28.6139, 77.2090, 28.6230, 77.2090,
                                      traffic_multiplier=2.0)
        assert abs(t_heavy - 2 * t_normal) < 1e-9

    def test_zero_speed_returns_inf(self):
        t = travel_time_minutes(28.6, 77.2, 28.7, 77.3, speed_kmh=0)
        assert t == float("inf")
