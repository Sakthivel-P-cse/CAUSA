from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from dashboard.backend.routers.traces import search_traces


def test_error_trace_search_uses_absolute_window():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    tempo = Mock()

    search_traces(service="checkout", error_only=True, minutes=30, limit=20, start=start, end=end, tempo=tempo)

    tempo.search_error_traces.assert_called_once_with(
        service="checkout", lookback_minutes=30, limit=20, start=start, end=end
    )


def test_error_trace_search_without_window_keeps_relative_lookback():
    tempo = Mock()

    search_traces(service="checkout", error_only=True, minutes=30, limit=20, start=None, end=None, tempo=tempo)

    tempo.search_error_traces.assert_called_once_with(
        service="checkout", lookback_minutes=30, limit=20
    )
