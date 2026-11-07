from app.rules import Event
from app.storage import EventStore


def test_insert_and_read_event(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    event = Event(
        ts=100.0,
        zone="Zone A",
        type="capacity",
        severity="critical",
        message="Zone A over capacity (5/4)",
        count=5,
    )

    event_id = store.add(event, snapshot="snap.jpg")
    rows = store.recent()

    assert event_id == 1
    assert store.count() == 1
    assert rows[0]["zone"] == "Zone A"
    assert rows[0]["severity"] == "critical"
    assert rows[0]["snapshot"] == "snap.jpg"


def test_recent_returns_newest_first(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.add(Event(1.0, "A", "warning", "warning", "first", 1))
    store.add(Event(2.0, "B", "capacity", "critical", "second", 2))

    rows = store.recent()

    assert [row["message"] for row in rows] == ["second", "first"]
