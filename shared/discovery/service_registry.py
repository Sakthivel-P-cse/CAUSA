from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import TraceSpan

OnNewService = Callable[[str], None]


class ServiceRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: set[str] = set()
        self._listeners: list[OnNewService] = []

    def add_listener(self, listener: OnNewService) -> None:
        with self._lock:
            self._listeners.append(listener)

    def known_services(self) -> set[str]:
        with self._lock:
            return set(self._services)

    def register(self, service: str | None) -> bool:
        if not service or not str(service).strip():
            return False
        name = str(service).strip()
        if name.lower() in {"unknown", "none", "n/a"}:
            return False
        with self._lock:
            if name in self._services:
                return False
            self._services.add(name)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(name)
            except Exception:
                pass
        return True

    def observe_spans(self, spans: list[TraceSpan]) -> None:
        for span in spans:
            self.register(span.service)


_registry = ServiceRegistry()


def get_registry() -> ServiceRegistry:
    return _registry
