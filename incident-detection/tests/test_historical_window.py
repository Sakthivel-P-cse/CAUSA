from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from ingest.reader import TelemetryReader
from ingest.clients.tempo_client import TempoClient


START = datetime(2020, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(seconds=90, microseconds=123)


@pytest.mark.parametrize('errors,fallback,expected', [(['error'], ['other'], 'error'), ([], ['historical'], 'historical'), ([], [], None)])
def test_historical_trace_selection_uses_incident_bounds(errors, fallback, expected):
    prom, tempo = Mock(), Mock()
    prom.get_service_edges.return_value = []
    prom.get_infra_nodes.return_value = []
    tempo.search_error_traces.return_value = errors
    tempo.search_traces_in_window.side_effect = [fallback, []] if not errors else [[]]
    tempo.get_trace.return_value = []
    reader = TelemetryReader(prom, tempo)
    result = reader.read_incident_window(incident_id='i', alerted_service='svc', window_start=START, window_end=END, include_metrics=False, include_logs=False)
    assert (result.window_start, result.window_end, result.trace_id) == (START, END, expected)
    tempo.search_error_traces.assert_called_once_with(service='svc', lookback_minutes=10, limit=1, start=START, end=END)
    if not errors:
        assert tempo.search_traces_in_window.call_args_list[0].kwargs == dict(service='svc', start=START, end=END, limit=1)
    assert 'current' not in [call.args[0] for call in tempo.get_trace.call_args_list]


def test_preferred_trace_skips_selection():
    prom, tempo = Mock(), Mock()
    prom.get_service_edges.return_value = prom.get_infra_nodes.return_value = []
    tempo.search_traces_in_window.return_value = []
    tempo.get_trace.return_value = []
    result = TelemetryReader(prom, tempo).read_incident_window(incident_id='i', alerted_service='svc', window_start=START, window_end=END, preferred_trace_id='chosen', include_metrics=False, include_logs=False)
    assert result.trace_id == 'chosen'
    tempo.search_error_traces.assert_not_called()
    tempo.get_trace.assert_called_once_with('chosen')


@pytest.mark.parametrize('start,end', [(START, None), (None, END), (END, START), (START, START)])
def test_invalid_explicit_windows_are_rejected(start, end):
    with pytest.raises(ValueError):
        TelemetryReader(Mock(), Mock()).read_incident_window(incident_id='i', alerted_service='svc', window_start=start, window_end=end)


def test_tempo_error_search_accepts_exact_bounds():
    client = TempoClient('http://localhost')
    client._search_traceql = Mock(return_value=['error'])
    try:
        assert client.search_error_traces('svc', start=START, end=END) == ['error']
        assert client._search_traceql.call_args.args[1:3] == (START, END)
    finally:
        client.close()
