"""Threshold rules that turn zone metrics into operator events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.zones import ZoneStats


@dataclass
class Event:
    """One thing an operator should look at, ready to store and display."""

    ts: float
    zone: str
    type: str
    severity: str
    message: str
    count: int


class RuleEngine:
    """Decides when zone metrics are worth raising as an event."""

    def __init__(self, config: dict) -> None:
        self.warning_ratio = float(config.get("warning_ratio", 0.80))
        self.alert_seconds = float(config.get("alert_seconds", 3.0))
        self.dwell_seconds = float(config.get("dwell_seconds", 30.0))
        self.cooldown_seconds = float(config.get("cooldown_seconds", 15.0))

        self._over_since: dict[str, float] = {}
        self._warning_since: dict[str, float] = {}
        self._last_fired: dict[tuple[str, str], float] = {}

    def _cooldown_ok(self, zone: str, event_type: str, now: float) -> bool:
        """True if this (zone, type) pair is allowed to fire again."""
        key = (zone, event_type)
        last = self._last_fired.get(key)
        # allow and stamp; this is the only write to last-fired
        if last is None or now - last >= self.cooldown_seconds:
            self._last_fired[key] = now
            return True
        return False

    def evaluate(self, stats: Iterable[ZoneStats] | dict[str, ZoneStats], now: float) -> list[Event]:
        """Return the events that should be recorded at this moment."""
        zones = stats.values() if isinstance(stats, dict) else stats
        events: list[Event] = []

        for zone in zones:
            capacity = max(1, zone.capacity)
            # a ratio, so each zone warns at its own count
            warning_at = self.warning_ratio * capacity

            # capacity rule
            if zone.count >= capacity:
                if zone.name not in self._over_since:
                    self._over_since[zone.name] = now
                elif (
                    now - self._over_since[zone.name] >= self.alert_seconds
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
                # back under capacity: the debounce must restart
                self._over_since.pop(zone.name, None)

            # strict upper bound keeps warning and capacity mutually exclusive
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
                # left the band: the debounce must restart
                self._warning_since.pop(zone.name, None)

            # dwell is already a duration, so it only needs the cooldown
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
