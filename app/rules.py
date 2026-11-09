"""Threshold rules that turn zone metrics into operator events.

A rule fires only after a condition has held for a configured duration,
and a cooldown stops the same alert from repeating every frame.
"""

# TEACHING NOTE: defers evaluation of type hints.
from __future__ import annotations

from dataclasses import dataclass   # auto-generates __init__ for Event
from typing import Iterable         # type hint only

# RuleEngine only needs the metric container, never the engine itself.
# This one-way import is why rules.py is testable with hand-built ZoneStats.
from app.zones import ZoneStats


@dataclass
class Event:
    """One thing an operator should look at, ready to store and display."""

    ts: float       # unix timestamp (seconds) when it fired
    zone: str       # which zone
    type: str       # "warning" | "capacity" | "dwell"
    severity: str   # "warning" | "critical" | "info" — drives the UI colour
    message: str    # human-readable sentence shown in the log
    count: int      # how many people were in the zone at that moment


class RuleEngine:
    """Decides when zone metrics are worth raising as an event."""

    def __init__(self, config: dict) -> None:
        # All four thresholds come from config; nothing is hard-coded here.
        # warning_ratio: fraction of capacity that counts as "filling up" (0.6 demo)
        self.warning_ratio = float(config.get("warning_ratio", 0.80))
        # alert_seconds: how long a condition must persist before firing (1.5 demo)
        self.alert_seconds = float(config.get("alert_seconds", 3.0))
        # dwell_seconds: how long one person may stay before a dwell event (30 demo)
        self.dwell_seconds = float(config.get("dwell_seconds", 30.0))
        # cooldown_seconds: minimum gap between two events of the same kind (30 demo)
        self.cooldown_seconds = float(config.get("cooldown_seconds", 15.0))

        # State that persists across frames. Each is "when did this start?"
        # _over_since: zone name -> time the zone first went at/over capacity
        self._over_since: dict[str, float] = {}
        # _warning_since: zone name -> time the zone first entered the warning band
        self._warning_since: dict[str, float] = {}
        # _last_fired: (zone, event_type) -> time that pair last produced an event
        self._last_fired: dict[tuple[str, str], float] = {}

    def _cooldown_ok(self, zone: str, event_type: str, now: float) -> bool:
        """True if this (zone, type) pair is allowed to fire again.

        NOTE: this mutates state when it returns True — it stamps `now` as the
        last-fired time. So it must only be called once the condition is
        already satisfied, never speculatively.
        """
        key = (zone, event_type)
        last = self._last_fired.get(key)
        # Never fired before, or enough time has passed -> allow, and record now.
        if last is None or now - last >= self.cooldown_seconds:
            self._last_fired[key] = now
            return True
        # Still inside the cooldown window -> suppress.
        return False

    def evaluate(self, stats: Iterable[ZoneStats] | dict[str, ZoneStats], now: float) -> list[Event]:
        """Return the events that should be recorded at this moment."""
        # Accept either a dict of name->ZoneStats or a plain iterable, so tests
        # can pass a simple list.
        zones = stats.values() if isinstance(stats, dict) else stats
        events: list[Event] = []

        for zone in zones:
            # max(1, ...) prevents a zero-capacity config from dividing by zero
            # or making every count an instant capacity breach.
            capacity = max(1, zone.capacity)
            # The count at which this zone starts warning, e.g. 0.6 * 8 = 4.8.
            # Counts are integers, so this really means "5 or more" for that zone.
            warning_at = self.warning_ratio * capacity

            # ---------- capacity rule ----------
            if zone.count >= capacity:
                if zone.name not in self._over_since:
                    # First frame over capacity: start the stopwatch, do not fire.
                    self._over_since[zone.name] = now
                elif (
                    # The condition has now held long enough...
                    now - self._over_since[zone.name] >= self.alert_seconds
                    # ...and this zone has not alerted recently.
                    and self._cooldown_ok(zone.name, "capacity", now)
                ):
                    events.append(
                        Event(
                            ts=now,
                            zone=zone.name,
                            type="capacity",
                            severity="critical",
                            message=f"{zone.name} over capacity ({zone.count}/{capacity})",
                            count=zone.count,
                        )
                    )
            else:
                # Dropped back under capacity: forget the start time so it must
                # re-accumulate for alert_seconds all over again.
                self._over_since.pop(zone.name, None)

            # ---------- warning rule ----------
            # Strict upper bound (< capacity) makes this mutually exclusive with
            # the capacity rule: one frame can never produce both.
            if warning_at <= zone.count < capacity:
                if zone.name not in self._warning_since:
                    self._warning_since[zone.name] = now
                elif (
                    now - self._warning_since[zone.name] >= self.alert_seconds
                    and self._cooldown_ok(zone.name, "warning", now)
                ):
                    events.append(
                        Event(
                            ts=now,
                            zone=zone.name,
                            type="warning",
                            severity="warning",
                            message=f"{zone.name} approaching capacity ({zone.count}/{capacity})",
                            count=zone.count,
                        )
                    )
            else:
                self._warning_since.pop(zone.name, None)

            # ---------- dwell rule ----------
            # Different shape: no duration gate, because max_dwell IS the
            # duration. It just needs to be over the threshold, and to respect
            # the cooldown so a stationary crowd does not fire every frame.
            if zone.max_dwell >= self.dwell_seconds and self._cooldown_ok(
                zone.name, "dwell", now
            ):
                events.append(
                    Event(
                        ts=now,
                        zone=zone.name,
                        type="dwell",
                        severity="info",
                        message=(
                            f"{zone.name} dwell {zone.max_dwell:.0f}s exceeds "
                            f"{self.dwell_seconds:.0f}s threshold"
                        ),
                        count=zone.count,
                    )
                )

        return events
