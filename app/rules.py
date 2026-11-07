"""Threshold rules that turn zone metrics into operator events.

A rule fires only after a condition has held for a configured duration,
and a cooldown stops the same alert from repeating every frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.zones import ZoneStats


@dataclass
class Event:
    ts: float
    zone: str
    type: str
    severity: str
    message: str
    count: int


class RuleEngine:
    def __init__(self, config: dict) -> None:
        self.warning_ratio = float(config.get("warning_ratio", 0.80))
        self.alert_seconds = float(config.get("alert_seconds", 3.0))
        self.dwell_seconds = float(config.get("dwell_seconds", 30.0))
        self.cooldown_seconds = float(config.get("cooldown_seconds", 15.0))

        self._over_since: dict[str, float] = {}
        self._warning_since: dict[str, float] = {}
        self._last_fired: dict[tuple[str, str], float] = {}

    def _cooldown_ok(self, zone: str, event_type: str, now: float) -> bool:
        key = (zone, event_type)
        last = self._last_fired.get(key)
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
            warning_at = self.warning_ratio * capacity

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
                self._over_since.pop(zone.name, None)

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
