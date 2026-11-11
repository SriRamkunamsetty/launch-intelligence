"""Prometheus Monitoring & Observability Exporter.
Tracks HTTP requests, latencies, ML inference times, and claim verification rates.
"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, List, Optional, Tuple


class MetricsRegistry:
    """Thread-safe Prometheus metrics registry in pure Python."""

    def __init__(self):
        self._lock = Lock()
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = defaultdict(float)
        self._histograms: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], List[float]] = defaultdict(list)

    def inc_counter(self, name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        lbl = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._counters[(name, lbl)] += value

    def set_gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        lbl = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._gauges[(name, lbl)] = value

    def observe_histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        lbl = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._histograms[(name, lbl)].append(value)

    def generate_prometheus_text(self) -> str:
        lines: List[str] = []
        with self._lock:
            # Counters
            for (name, lbl), val in sorted(self._counters.items()):
                lbl_str = ",".join(f'{k}="{v}"' for k, v in lbl)
                lbl_formatted = f"{{{lbl_str}}}" if lbl_str else ""
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{lbl_formatted} {val}")

            # Gauges
            for (name, lbl), val in sorted(self._gauges.items()):
                lbl_str = ",".join(f'{k}="{v}"' for k, v in lbl)
                lbl_formatted = f"{{{lbl_str}}}" if lbl_str else ""
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name}{lbl_formatted} {val}")

            # Histograms (count and sum)
            for (name, lbl), observations in sorted(self._histograms.items()):
                lbl_str = ",".join(f'{k}="{v}"' for k, v in lbl)
                lbl_formatted = f"{{{lbl_str}}}" if lbl_str else ""
                lines.append(f"# TYPE {name} histogram")
                lines.append(f"{name}_count{lbl_formatted} {len(observations)}")
                lines.append(f"{name}_sum{lbl_formatted} {sum(observations):.6f}")

        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()


class Timer:
    """Context manager for tracking execution time in a histogram."""

    def __init__(self, metric_name: str, labels: Optional[Dict[str, str]] = None):
        self.metric_name = metric_name
        self.labels = labels or {}
        self.start = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.perf_counter() - self.start
        REGISTRY.observe_histogram(self.metric_name, duration, self.labels)
