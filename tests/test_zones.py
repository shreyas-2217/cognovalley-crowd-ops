import pytest

from app.zones import Track, ZoneEngine, point_in_polygon


def make_zone() -> dict:
    return {
        "name": "Test Zone",
        "polygon": [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]],
        "capacity": 3,
    }


def make_track(track_id: int, cx: float, cy: float) -> Track:
    return Track(track_id, cx - 5, cy - 5, cx + 5, cy + 5)


def test_point_in_polygon_inside_outside_and_edge() -> None:
    polygon = [(25, 25), (75, 25), (75, 75), (25, 75)]

    assert point_in_polygon(50, 50, polygon) is True
    assert point_in_polygon(10, 10, polygon) is False
    assert point_in_polygon(25, 50, polygon) is True


def test_occupancy_counts_only_tracks_inside_zone() -> None:
    engine = ZoneEngine([make_zone()], pixels_per_meter=50)

    stats = engine.update(
        [make_track(1, 50, 50), make_track(2, 60, 60), make_track(3, 10, 10)],
        now=0.0,
        frame_w=100,
        frame_h=100,
    )

    assert stats["Test Zone"].count == 2


def test_density_is_people_per_square_metre() -> None:
    engine = ZoneEngine([make_zone()], pixels_per_meter=50)

    stats = engine.update(
        [make_track(1, 50, 50), make_track(2, 60, 60)],
        now=0.0,
        frame_w=100,
        frame_h=100,
    )

    # 50x50 px at 50 px/m = 1 m^2, so two people = 2 people/m^2.
    assert stats["Test Zone"].density == pytest.approx(2.0)


def test_dwell_accumulates_and_resets_after_leaving() -> None:
    engine = ZoneEngine([make_zone()], pixels_per_meter=50)

    engine.update([make_track(1, 50, 50)], now=0.0, frame_w=100, frame_h=100)
    stats = engine.update([make_track(1, 50, 50)], now=5.0, frame_w=100, frame_h=100)
    assert stats["Test Zone"].max_dwell == pytest.approx(5.0)

    engine.update([make_track(1, 10, 10)], now=6.0, frame_w=100, frame_h=100)
    stats = engine.update([make_track(1, 50, 50)], now=10.0, frame_w=100, frame_h=100)

    assert stats["Test Zone"].max_dwell == pytest.approx(0.0)
