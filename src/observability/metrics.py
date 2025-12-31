from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


def _now() -> float:
    return time.perf_counter()


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: Mapping[str, str] | None) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape_label_value(v)}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _key(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _sanitize_metric_name(name: str) -> str:
    out = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    sanitized = "".join(out)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


DEFAULT_HISTOGRAM_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


@dataclass(frozen=True)
class _HistogramSpec:
    buckets: tuple[float, ...]


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self._hist_specs: dict[str, _HistogramSpec] = {}
        self._hist_counts: dict[str, dict[tuple[tuple[str, str], ...], list[int]]] = defaultdict(dict)
        self._hist_sum: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self._hist_total: dict[str, dict[tuple[tuple[str, str], ...], int]] = defaultdict(lambda: defaultdict(int))

    def inc(self, name: str, *, value: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        metric = _sanitize_metric_name(name)
        with self._lock:
            self._counters[metric][_key(labels)] += float(value)

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
        buckets: Iterable[float] = DEFAULT_HISTOGRAM_BUCKETS,
    ) -> None:
        metric = _sanitize_metric_name(name)
        label_key = _key(labels)
        buckets_tuple = tuple(float(b) for b in buckets)
        with self._lock:
            spec = self._hist_specs.get(metric)
            if spec is None:
                self._hist_specs[metric] = _HistogramSpec(buckets=buckets_tuple)
            else:
                buckets_tuple = spec.buckets

            counts_by_label = self._hist_counts[metric]
            if label_key not in counts_by_label:
                counts_by_label[label_key] = [0 for _ in buckets_tuple]

            self._hist_total[metric][label_key] += 1
            self._hist_sum[metric][label_key] += float(value)
            for i, b in enumerate(buckets_tuple):
                if value <= b:
                    counts_by_label[label_key][i] += 1

    def render_prometheus(self) -> str:
        with self._lock:
            lines: list[str] = []

            for name in sorted(self._counters.keys()):
                lines.append(f"# TYPE {name} counter")
                for label_key, value in sorted(self._counters[name].items()):
                    labels = dict(label_key)
                    lines.append(f"{name}{_format_labels(labels)} {value}")

            for name in sorted(self._hist_specs.keys()):
                spec = self._hist_specs[name]
                lines.append(f"# TYPE {name} histogram")
                counts_by_label = self._hist_counts.get(name, {})

                for label_key in sorted(counts_by_label.keys()):
                    labels_base = dict(label_key)
                    counts = counts_by_label[label_key]
                    cumulative = 0
                    for b, c in zip(spec.buckets, counts, strict=True):
                        cumulative += int(c)
                        labels = {**labels_base, "le": str(b)}
                        lines.append(f"{name}_bucket{_format_labels(labels)} {cumulative}")
                    labels_inf = {**labels_base, "le": "+Inf"}
                    total = self._hist_total[name][label_key]
                    lines.append(f"{name}_bucket{_format_labels(labels_inf)} {total}")
                    lines.append(f"{name}_count{_format_labels(labels_base)} {total}")
                    lines.append(f"{name}_sum{_format_labels(labels_base)} {self._hist_sum[name][label_key]}")

            return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()


class Timer:
    def __init__(self, name: str, *, labels: Mapping[str, str] | None = None) -> None:
        self._name = name
        self._labels = dict(labels) if labels else None
        self._started = _now()

    def observe(self) -> float:
        duration = _now() - self._started
        REGISTRY.observe(self._name, duration, labels=self._labels)
        return duration


def inc(name: str, *, value: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
    REGISTRY.inc(name, value=value, labels=labels)


def observe(name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
    REGISTRY.observe(name, value, labels=labels)


def time_histogram(name: str, *, labels: Mapping[str, str] | None = None) -> Timer:
    return Timer(name, labels=labels)
