from app.rules import RuleEngine
from app.zones import ZoneStats


def zone(count: int, capacity: int = 2, max_dwell: float = 0.0) -> ZoneStats:
    return ZoneStats(
        name="Zone A",
        count=count,
        capacity=capacity,
        density=0.0,
        max_dwell=max_dwell,
        velocity=0.0,
    )


def engine() -> RuleEngine:
    return RuleEngine(
        {
            "warning_ratio": 0.80,
            "alert_seconds": 3.0,
            "dwell_seconds": 30.0,
            "cooldown_seconds": 10.0,
        }
    )


def test_no_capacity_event_before_duration_threshold() -> None:
    rules = engine()

    assert rules.evaluate([zone(2)], now=0.0) == []
    assert rules.evaluate([zone(2)], now=2.0) == []


def test_capacity_event_after_duration_threshold() -> None:
    rules = engine()
    rules.evaluate([zone(2)], now=0.0)

    events = rules.evaluate([zone(2)], now=3.1)

    assert len(events) == 1
    assert events[0].severity == "critical"
    assert events[0].type == "capacity"
    assert events[0].count == 2


def test_cooldown_suppresses_duplicate_event() -> None:
    rules = engine()
    rules.evaluate([zone(2)], now=0.0)
    first = rules.evaluate([zone(2)], now=3.1)
    duplicate = rules.evaluate([zone(2)], now=4.0)

    assert len(first) == 1
    assert duplicate == []


def test_warning_event_at_warning_ratio() -> None:
    rules = engine()
    stats = [zone(4, capacity=5)]

    rules.evaluate(stats, now=0.0)
    events = rules.evaluate(stats, now=3.1)

    assert len(events) == 1
    assert events[0].severity == "warning"
    assert events[0].type == "warning"


def test_dwell_event_when_threshold_crossed() -> None:
    rules = engine()

    events = rules.evaluate([zone(1, capacity=10, max_dwell=31.0)], now=31.0)

    assert len(events) == 1
    assert events[0].severity == "info"
    assert events[0].type == "dwell"
