from datetime import UTC, datetime, timedelta

from app.application.activity import ActivityTracker

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def test_touch_then_active_within_window():
    tracker = ActivityTracker()
    tracker.touch("s1", 1, NOW)

    assert tracker.active(NOW, timedelta(minutes=30)) == [("s1", 1)]


def test_active_excludes_sessions_outside_the_window():
    tracker = ActivityTracker()
    tracker.touch("s1", 1, NOW - timedelta(minutes=31))

    assert tracker.active(NOW, timedelta(minutes=30)) == []


def test_active_includes_a_session_exactly_at_the_boundary():
    tracker = ActivityTracker()
    tracker.touch("s1", 1, NOW - timedelta(minutes=30))

    assert tracker.active(NOW, timedelta(minutes=30)) == [("s1", 1)]


def test_touch_overwrites_previous_last_seen():
    tracker = ActivityTracker()
    tracker.touch("s1", 1, NOW - timedelta(hours=1))
    tracker.touch("s1", 1, NOW)

    assert tracker.active(NOW, timedelta(minutes=1)) == [("s1", 1)]


def test_multiple_sessions_are_tracked_independently():
    tracker = ActivityTracker()
    tracker.touch("s1", 1, NOW)
    tracker.touch("s2", 2, NOW - timedelta(hours=2))

    assert tracker.active(NOW, timedelta(minutes=30)) == [("s1", 1)]


def test_forget_removes_a_session():
    tracker = ActivityTracker()
    tracker.touch("s1", 1, NOW)

    tracker.forget("s1")

    assert tracker.active(NOW, timedelta(minutes=30)) == []


def test_forget_unknown_session_is_a_noop():
    tracker = ActivityTracker()
    tracker.forget("never-seen")  # must not raise
