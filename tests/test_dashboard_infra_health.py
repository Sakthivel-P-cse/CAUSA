from dashboard.backend.routers.graphs import _infra_health


def classify(**overrides):
    values = {
        "kind": "pod",
        "namespace": "demo",
        "condition": "Running",
        "restart_count": 0,
        "cpu": 10.0,
        "memory": 20.0,
        "service_name": "checkout",
        "active_services": {"checkout"},
    }
    values.update(overrides)
    return _infra_health(**values)


def test_active_application_telemetry_is_healthy():
    assert classify()[:2] == ("ok", "telemetry_active")


def test_missing_application_telemetry_is_error():
    assert classify(active_services=set())[:2] == ("error", "no_telemetry")


def test_system_pod_does_not_require_application_telemetry():
    assert classify(namespace="kube-system", active_services=set(), restart_count=1)[:2] == (
        "infra",
        "infrastructure",
    )


def test_fault_takes_priority_over_telemetry():
    assert classify(restart_count=4)[:2] == ("error", "fault")


def test_unknown_telemetry_does_not_create_false_error():
    assert classify(active_services=None)[:2] == ("unknown", "unknown")
