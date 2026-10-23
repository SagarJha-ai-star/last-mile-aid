"""
Simulated Clock for deterministic scenario testing and accelerated demo playback.

Supports speed multipliers (1x, 5x, 10x, 60x) so a 75-minute rescue scenario
can be observed by judges in ~2 minutes.
"""

from __future__ import annotations

import time
from typing import Callable, Optional


class SimulatedClock:
    """
    Simulated timeline tracker.
    Times are measured in simulated minutes from t=0.0.
    """

    def __init__(self, start_minutes: float = 0.0):
        self.current_minutes: float = start_minutes
        self.multiplier: float = 1.0  # 1x real time by default
        self.is_running: bool = False
        self._last_real_time: Optional[float] = None
        self._tick_listeners: list[Callable[[float], None]] = []

    def set_multiplier(self, multiplier: float) -> None:
        """Change speed: 1.0, 5.0, 10.0, 60.0."""
        self.multiplier = max(multiplier, 0.1)

    def advance_minutes(self, delta_minutes: float) -> float:
        """Manually advance clock by delta_minutes."""
        self.current_minutes += delta_minutes
        self._notify()
        return self.current_minutes

    def reset(self, start_minutes: float = 0.0) -> None:
        """Reset simulated time to start_minutes."""
        self.current_minutes = start_minutes
        self._last_real_time = time.time()
        self._notify()

    def tick_realtime(self) -> float:
        """
        Advance simulated clock based on elapsed real-world time and multiplier.
        Called on periodic background ticks or requests.
        """
        now = time.time()
        if self._last_real_time is None:
            self._last_real_time = now
            return self.current_minutes

        delta_real_seconds = now - self._last_real_time
        self._last_real_time = now

        # Convert delta real seconds to simulated minutes
        # 1 real second = (multiplier / 60) simulated minutes
        delta_sim_minutes = (delta_real_seconds * self.multiplier) / 60.0
        self.current_minutes += delta_sim_minutes
        self._notify()
        return self.current_minutes

    def subscribe(self, callback: Callable[[float], None]) -> None:
        """Register a callback for every clock tick."""
        self._tick_listeners.append(callback)

    def _notify(self) -> None:
        for cb in self._tick_listeners:
            try:
                cb(self.current_minutes)
            except Exception:
                pass
