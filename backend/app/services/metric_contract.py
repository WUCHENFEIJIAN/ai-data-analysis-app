"""Strict metric semantics shared by analysis artifacts and report validation.

The analysis stage owns values and formulas.  Report planning may select a metric,
but it must not reinterpret its denominator or aggregation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any, Literal, get_args

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class MetricValidationError(ValueError):
    """Raised when a metric's label, formula, values or provenance disagree."""


MetricSemanticType = Literal[
    "measure", "count", "rate", "ratio", "duration", "score", "quantity"
]
UnitFamily = Literal[
    "currency", "count", "percentage", "duration", "ratio", "score", "quantity"
]
CountSemantics = Literal[
    "row_count", "field_sum", "distinct_count", "event_count", "entity_count"
]
RatioBasis = Literal[
    "per_entity",
    "per_event",
    "per_row",
    "per_quantity",
    "other",
]
RatioValueBasis = Literal["fraction", "percent"]
MetricScope = Literal["reusable_measure", "scalar_evidence"]


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,119}$",
        validation_alias=AliasChoices("metric_id", "id"),
        description=(
            "Canonical metric identifier. Always emit this field as 'metric_id'; 'id' is "
            "accepted only as a narrow input compatibility alias."
        ),
    )
    metric_scope: MetricScope = Field(
        default="scalar_evidence",
        description=(
            "Use reusable_measure for a metric concept that may bind a measure field across "
            "dimension values. Use scalar_evidence for a specific observation, comparison, "
            "period, category, entity, or snapshot used only as Claim evidence."
        ),
    )
    label: str = Field(min_length=1, max_length=200)
    value: float | None = Field(
        default=None,
        description=(
            "Materialized scalar value required for scalar_evidence. Omit or set null for "
            "reusable_measure, whose values are represented by the source field series."
        ),
    )
    aggregation: str = Field(
        min_length=1,
        max_length=60,
        description=(
            "Aggregation used to compute the value. Use 'ratio' only for numerator / "
            "denominator metrics."
        ),
    )
    semantic_type: MetricSemanticType
    unit_family: UnitFamily
    scale: float = Field(default=1, gt=0)
    numerator: str | None = Field(default=None, max_length=120)
    denominator: str | None = Field(default=None, max_length=120)
    ratio_basis: RatioBasis | None = Field(
        default=None,
        description=(
            "Required when aggregation is 'ratio'; must be null or omitted for every "
            "non-ratio metric."
        ),
    )
    ratio_value_basis: RatioValueBasis | None = Field(
        default=None,
        description=(
            "Canonical numeric representation for percentage values: fraction means 0.081, "
            "percent means 8.1. Percentage numerator / denominator ratios default to fraction."
        ),
    )
    unit: str = Field(default="", max_length=40)
    count_semantics: CountSemantics | None = None
    grain: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        description="Verified aggregation grain; omit when the source grain is unknown.",
    )
    is_distinct: bool | None = None
    definition: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Human-readable computation definition. For aggregation='ratio', this MUST "
            "contain the literal '/' character between numerator and denominator."
        ),
    )
    source_artifact: str = Field(min_length=1, max_length=300)
    source_field: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "Physical table field or dot-delimited JSON object path containing the metric "
            "value or series in source_artifact."
        ),
    )
    source_selector: dict[str, str | int | float | bool] | None = Field(
        default=None,
        description=(
            "Exact field/value selector locating the scalar observation in a tabular source."
        ),
    )
    # Direct values are meaningful only for scalar_evidence. Reusable measures are series
    # concepts and must not be scalarized into a placeholder or one representative value.
    numerator_value: float | None = None
    denominator_value: float | None = None
    tolerance: float = Field(default=1e-4, gt=0, le=0.1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_input(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "metric_id" in value and "id" in value:
            raise ValueError(
                "MetricDefinition cannot contain both 'metric_id' and its input alias 'id'"
            )
        if isinstance(value, Mapping) and value.get("ratio_basis") in {"fraction", "percent"}:
            normalized = dict(value)
            explicit_value_basis = normalized.get("ratio_value_basis")
            if explicit_value_basis not in {None, normalized["ratio_basis"]}:
                raise ValueError("ratio_basis and ratio_value_basis representations conflict")
            normalized.setdefault("ratio_value_basis", normalized["ratio_basis"])
            if str(normalized.get("aggregation", "")).lower() == "ratio":
                normalized["ratio_basis"] = "other"
            else:
                normalized.pop("ratio_basis", None)
            return normalized
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> MetricDefinition:
        if self.metric_scope == "scalar_evidence" and self.value is None:
            raise ValueError("scalar_evidence metrics require a materialized value")
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError("metric value must be finite")
        for name, value in (
            ("numerator_value", self.numerator_value),
            ("denominator_value", self.denominator_value),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.aggregation.lower() == "ratio":
            if not self.numerator or not self.denominator:
                raise ValueError("ratio metrics require numerator and denominator")
            if "/" not in self.definition and "÷" not in self.definition:
                raise ValueError("ratio metric definition must state numerator / denominator")
            if self.ratio_basis is None:
                raise ValueError("ratio metrics require ratio_basis")
        elif self.ratio_basis is not None:
            raise ValueError("ratio_basis is only valid for ratio metrics")
        if self.ratio_value_basis is not None and self.unit_family != "percentage":
            raise ValueError("ratio_value_basis requires percentage unit_family")
        is_count = self.semantic_type == "count" or self.unit_family == "count"
        if is_count:
            if self.semantic_type != "count" or self.unit_family != "count":
                raise ValueError("count metrics must use count semantic type and unit family")
            if self.count_semantics is None or self.is_distinct is None:
                raise ValueError("count metrics require count_semantics and is_distinct")
            if self.count_semantics == "distinct_count" and not self.is_distinct:
                raise ValueError("distinct_count metrics must set is_distinct=true")
            if (
                self.count_semantics in {"row_count", "field_sum", "event_count"}
                and self.is_distinct
            ):
                raise ValueError(
                    f"{self.count_semantics} metrics must set is_distinct=false"
                )
        elif self.count_semantics is not None or self.is_distinct is not None:
            raise ValueError("count semantics are only valid for count metrics")
        if self.unit_family == "percentage" and self.semantic_type not in {"rate", "ratio"}:
            raise ValueError("percentage metrics must use rate or ratio semantic type")
        return self

    @property
    def source(self) -> str:
        """Human-friendly alias used by report consumers."""

        return self.source_artifact


# Internal semantic vocabulary. These token names describe how a value is represented or
# classified inside the pipeline. They are never user-visible display units. The set is built
# from this module's own Literal vocabularies, so it stays dataset independent.
INTERNAL_SEMANTIC_TOKENS: frozenset[str] = frozenset(
    {
        *get_args(RatioValueBasis),
        *get_args(RatioBasis),
        *get_args(UnitFamily),
        *get_args(MetricSemanticType),
        *get_args(CountSemantics),
        *get_args(MetricScope),
        "aggregation",
        "ratio_value_basis",
        "ratio_basis",
        "unit_family",
        "semantic_type",
        "metric_scope",
        "count_semantics",
    }
)


def is_internal_semantic_token(value: str | None) -> bool:
    """Return True when a string is an internal representation name, not a display unit."""

    return (value or "").strip().lower() in INTERNAL_SEMANTIC_TOKENS


def metric_display_unit(metric: MetricDefinition) -> str:
    """Return the user-visible unit suffix for a metric.

    The Metric Contract's `unit` field is authored upstream and sometimes carries an
    internal representation name instead of a display unit. Presentation must never leak
    those names, so they resolve to an empty display unit here. Percentage metrics also
    resolve to an empty unit because the percent formatter owns the '%' suffix.
    """

    if metric.unit_family == "percentage":
        return ""
    declared = (metric.unit or "").strip()
    if is_internal_semantic_token(declared):
        return ""
    return declared


def metric_display_unit_matches(metric: MetricDefinition, unit: str | None) -> bool:
    """Check an authored presentation unit against the canonical display boundary.

    A legacy percent sign is accepted at evidence declaration boundaries for compatibility,
    but percentage report specs canonicalize it to an empty unit because the percent formatter
    owns the suffix. Internal representation names are never accepted.
    """

    declared = (unit or "").strip()
    expected = metric_display_unit(metric)
    if metric.unit_family == "percentage" and declared == "%":
        return True
    return declared == expected


def metric_display_scale(metric: MetricDefinition) -> float:
    """Return the presentation divisor derived from verified metric semantics."""

    if metric_ratio_value_basis(metric) == "fraction":
        return metric.scale * 0.01
    return metric.scale


def metric_ratio_value_basis(metric: MetricDefinition) -> RatioValueBasis | None:
    """Return the canonical percentage representation without using denominator semantics."""

    if metric.unit_family != "percentage":
        return None
    if metric.ratio_value_basis is not None:
        return metric.ratio_value_basis
    if metric.aggregation.lower() == "ratio" and metric.numerator and metric.denominator:
        return "fraction"
    return "percent"


# The name used in the design document is Metric Contract.  Keep both names
# available so callers can choose the domain term or the schema term.
MetricContract = MetricDefinition


def _ratio_error(
    metric: MetricDefinition, definitions: dict[str, MetricDefinition]
) -> str | None:
    if metric.aggregation.lower() != "ratio":
        return None
    if metric.metric_scope == "reusable_measure":
        return _reusable_ratio_error(metric, definitions)
    numerator = metric.numerator_value
    denominator = metric.denominator_value
    if numerator is None and metric.numerator:
        numerator = (
            definitions[metric.numerator].value
            if metric.numerator in definitions
            else None
        )
    if denominator is None and metric.denominator:
        denominator = (
            definitions[metric.denominator].value
            if metric.denominator in definitions
            else None
        )
    # A contract may be published before its upstream metric definitions.  The
    # semantic shape is still checked; numerical validation waits for values.
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return "ratio denominator must not be zero"
    expected = numerator / denominator
    if metric_ratio_value_basis(metric) == "percent":
        expected *= 100
    allowed = max(1e-9, abs(expected) * metric.tolerance)
    if metric.value is None:
        return "scalar_evidence ratio requires a materialized value"
    if not math.isfinite(metric.value) or abs(metric.value - expected) > allowed:
        return (
            f"value {metric.value} does not match numerator / denominator {expected}. "
            "either correct the value or reference the actual derived numerator metric "
            "(for example, a delta rather than the current-period total)"
        )
    return None


def _reusable_ratio_error(
    metric: MetricDefinition, definitions: dict[str, MetricDefinition]
) -> str | None:
    """Validate reusable ratios by binding metadata, never a scalar value."""

    numerator = definitions.get(metric.numerator or "")
    denominator = definitions.get(metric.denominator or "")
    if numerator is None or denominator is None:
        return None
    if numerator.metric_scope != "reusable_measure":
        return (
            f"{metric.metric_id}: reusable ratio numerator {numerator.metric_id} must be "
            "reusable_measure"
        )
    if denominator.metric_scope != "reusable_measure":
        return (
            f"{metric.metric_id}: reusable ratio denominator {denominator.metric_id} must be "
            "reusable_measure"
        )
    if not metric.source_field:
        return f"{metric.metric_id}: reusable ratio source_field is required"
    if not numerator.source_field or not denominator.source_field:
        return f"{metric.metric_id}: reusable ratio references require source_field"
    if len({metric.source_artifact, numerator.source_artifact, denominator.source_artifact}) != 1:
        return (
            f"{metric.metric_id}: reusable ratio source_artifact must match numerator "
            "and denominator"
        )
    if metric.grain != numerator.grain or metric.grain != denominator.grain:
        return f"{metric.metric_id}: reusable ratio grain must match numerator and denominator"
    return None


def _ratio_basis_error(
    metric: MetricDefinition, definitions: dict[str, MetricDefinition]
) -> str | None:
    if metric.aggregation.lower() != "ratio" or metric.ratio_basis == "other":
        return None
    denominator = definitions.get(metric.denominator or "")
    if denominator is None:
        return None
    if metric.ratio_basis == "per_quantity":
        if denominator.unit_family != "quantity":
            return (
                "per_quantity ratio requires a quantity denominator. use ratio_basis "
                "'other' for a legitimate measure-to-measure ratio"
            )
        return None
    expected = {
        "per_entity": {"entity_count", "distinct_count"},
        "per_event": {"event_count", "row_count"},
        "per_row": {"row_count"},
    }.get(metric.ratio_basis or "", set())
    if denominator.count_semantics not in expected:
        return (
            f"{metric.ratio_basis} ratio is incompatible with denominator "
            f"count semantics {denominator.count_semantics}. use ratio_basis 'other' "
            "for a legitimate measure-to-measure ratio"
        )
    return None


class MetricValidator:
    """Validate a registry of metrics and their cross-metric ratio references."""

    @classmethod
    def issues(cls, metrics: Iterable[MetricDefinition]) -> list[str]:
        values = list(metrics)
        by_id: dict[str, MetricDefinition] = {}
        issues: list[str] = []
        for metric in values:
            if metric.metric_id in by_id:
                issues.append(f"duplicate metric_id: {metric.metric_id}")
            by_id[metric.metric_id] = metric
            if not metric.source_artifact:
                issues.append(f"{metric.metric_id}: source_artifact is required")
            ratio_error = _ratio_error(metric, by_id)
            if ratio_error:
                issues.append(f"{metric.metric_id}: {ratio_error}")
            basis_error = _ratio_basis_error(metric, by_id)
            if basis_error:
                issues.append(f"{metric.metric_id}: {basis_error}")
            if metric.aggregation.lower() == "ratio":
                if metric.numerator not in by_id:
                    # The registry may be ordered arbitrarily.  Defer unknown
                    # reference checking until the complete map is available.
                    pass
                if metric.denominator not in by_id:
                    pass
        for metric in values:
            if metric.aggregation.lower() != "ratio":
                continue
            if (
                metric.numerator
                and metric.numerator_value is None
                and metric.numerator not in by_id
            ):
                issues.append(f"{metric.metric_id}: unknown numerator metric {metric.numerator}")
            if (
                metric.denominator
                and metric.denominator_value is None
                and metric.denominator not in by_id
            ):
                issues.append(
                    f"{metric.metric_id}: unknown denominator metric {metric.denominator}"
                )
            ratio_error = _ratio_error(metric, by_id)
            if ratio_error and not any(
                metric.metric_id in issue and ratio_error in issue for issue in issues
            ):
                issues.append(f"{metric.metric_id}: {ratio_error}")
            basis_error = _ratio_basis_error(metric, by_id)
            if basis_error and not any(
                metric.metric_id in issue and basis_error in issue for issue in issues
            ):
                issues.append(f"{metric.metric_id}: {basis_error}")
        return issues

    @classmethod
    def validate(cls, metrics: Iterable[MetricDefinition]) -> None:
        issues = cls.issues(metrics)
        if issues:
            raise MetricValidationError("; ".join(issues))


def validate_metric_contracts(metrics: Iterable[MetricDefinition]) -> None:
    MetricValidator.validate(metrics)


def build_metric_registry(
    metrics: Iterable[MetricDefinition],
) -> tuple[dict[str, MetricDefinition], list[str]]:
    """Index metric definitions and report duplicate IDs without dropping later copies."""

    registry: dict[str, MetricDefinition] = {}
    issues: list[str] = []
    for metric in metrics:
        if metric.metric_id in registry:
            issues.append(f"duplicate metric_id: {metric.metric_id}")
        registry[metric.metric_id] = metric
    return registry, issues


def metric_reference_issues(
    *,
    owner: str,
    metric_id: str,
    unit: str | None = None,
    scale: float | int | None = None,
    inline_definition: MetricDefinition | None = None,
    registry: dict[str, MetricDefinition],
) -> list[str]:
    """Check that a KPI, series or column reference matches its Metric Definition."""

    issues: list[str] = []
    definition = registry.get(metric_id)
    if definition is None:
        issues.append(f"{owner} references unknown metric definition: {metric_id}")
        return issues
    if inline_definition is not None:
        if inline_definition.metric_id != metric_id:
            issues.append(
                f"{owner} inline metric_definition.metric_id "
                f"{inline_definition.metric_id} does not match referenced metric {metric_id}"
            )
        elif inline_definition.model_dump(mode="json") != definition.model_dump(mode="json"):
            issues.append(
                f"{owner} inline metric contract does not match metric definition {metric_id}"
            )
    expected_unit = metric_display_unit(definition)
    if unit is not None and not metric_display_unit_matches(definition, unit):
        issues.append(
            f"{owner} unit {unit!r} does not match metric definition {metric_id} "
            f"display unit {expected_unit!r}"
        )
    expected_scale = metric_display_scale(definition)
    if scale is not None and float(scale) != float(expected_scale):
        issues.append(
            f"{owner} scale {scale} does not match metric definition {metric_id} "
            f"scale {expected_scale}"
        )
    return issues

