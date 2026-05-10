"""Metrics registry — load metrics.yml and expose typed entries to Layer B.

In production this would be replaced by a dbt Semantic Layer / MetricFlow /
WrenAI MDL adapter. Here it's a hand-curated YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

CrossSourcePolicy = Literal["required", "optional", "disabled"]
Aggregator = Literal["SUM", "COUNT_DISTINCT", "RAW"]


@dataclass(frozen=True)
class SourceSpec:
    """One source definition (primary or alternative) for a metric."""

    table: str
    column: str
    period_column: str
    extra_filters: tuple[str, ...] = ()
    breakdown: str | None = None
    aggregator: Aggregator = "SUM"
    notes: str = ""


@dataclass(frozen=True)
class MetricEntry:
    """One metric definition from metrics.yml."""

    id: str
    metric_name: str
    cross_source: CrossSourcePolicy
    period_start: str  # 'YYYY-MM-DD' inclusive
    period_end: str    # 'YYYY-MM-DD' exclusive
    primary: SourceSpec
    alternatives: tuple[SourceSpec, ...] = ()
    disagreement_threshold_pct: float = 1.0
    notes_for_layer_b: str = ""
    # Plausibility bounds — Layer A flags FAIL when any breakdown value falls
    # outside [expected_min, expected_max]. Both optional; skip the check when
    # either is None. Hand-curated; widen generously to avoid false positives.
    expected_min: float | None = None
    expected_max: float | None = None

    @property
    def has_breakdown(self) -> bool:
        return self.primary.breakdown is not None


@dataclass(frozen=True)
class MetricsRegistry:
    entries: dict[str, MetricEntry] = field(default_factory=dict)

    def get(self, question_id: str) -> MetricEntry | None:
        return self.entries.get(question_id)


def load_metrics_registry(path: Path | None = None) -> MetricsRegistry:
    """Load metrics.yml from project root (default) or a custom path."""
    metrics_path = path or _default_metrics_path()
    if not metrics_path.is_file():
        return MetricsRegistry(entries={})
    raw = yaml.safe_load(metrics_path.read_text(encoding="utf-8")) or {}
    entries: dict[str, MetricEntry] = {}
    for raw_entry in raw.get("metrics", []):
        entry = _build_entry(raw_entry)
        entries[entry.id] = entry
    return MetricsRegistry(entries=entries)


def _default_metrics_path() -> Path:
    # __file__ = src/pluang_agent/metrics.py → project root is two levels up
    return Path(__file__).parents[2] / "metrics.yml"


def _build_entry(raw: dict) -> MetricEntry:
    return MetricEntry(
        id=raw["id"],
        metric_name=raw["metric_name"],
        cross_source=raw["cross_source"],
        period_start=raw["period_start"],
        period_end=raw["period_end"],
        primary=_build_source(raw["primary"]),
        alternatives=tuple(_build_source(alt) for alt in raw.get("alternatives") or []),
        disagreement_threshold_pct=float(raw.get("disagreement_threshold_pct", 1.0)),
        notes_for_layer_b=raw.get("notes_for_layer_b", ""),
        expected_min=_optional_float(raw.get("expected_min")),
        expected_max=_optional_float(raw.get("expected_max")),
    )


def _optional_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _build_source(raw: dict) -> SourceSpec:
    return SourceSpec(
        table=raw["table"],
        column=raw["column"],
        period_column=raw["period_column"],
        extra_filters=tuple(raw.get("extra_filters") or []),
        breakdown=raw.get("breakdown"),
        aggregator=raw.get("aggregator", "SUM"),
        notes=raw.get("notes", ""),
    )
