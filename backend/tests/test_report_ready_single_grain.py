from app.services.metric_contract import MetricDefinition
from app.services.report_ready_artifacts import (
    ReportReadyArtifact,
    validate_report_ready_artifacts,
)
from app.services.workspace import PathResolver


def _metric(metric_id: str, grain: str, source: str) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        metric_scope="reusable_measure",
        label=metric_id,
        value=0.5,
        aggregation="mean",
        semantic_type="rate",
        unit_family="percentage",
        ratio_value_basis="fraction",
        grain=grain,
        definition=f"Verified {metric_id}",
        source_artifact=source,
    )


def test_report_ready_artifact_rejects_mixed_overall_and_dimension_grain(tmp_path) -> None:
    project_id = "pj_" + "c" * 32
    resolver = PathResolver(tmp_path)
    target = resolver.resolve(project_id, "data/mixed.csv")
    target.parent.mkdir(parents=True)
    target.write_text(
        "scope,region,delivery_rate,late_rate\n"
        "overall,,0.9999,0.081\n"
        "region,AL,,0.239\n"
        "region,MA,,0.199\n",
        encoding="utf-8",
    )
    declaration = ReportReadyArtifact.model_validate(
        {
            "artifact_path": "data/mixed.csv",
            "fields": [
                {"name": "region", "role": "dimension"},
                {"name": "delivery_rate", "role": "measure", "metric_ref": "delivery_rate"},
                {"name": "late_rate", "role": "measure", "metric_ref": "late_rate"},
            ],
        }
    )
    metrics = [
        _metric("delivery_rate", "overall", "data/mixed.csv"),
        _metric("late_rate", "region", "data/mixed.csv"),
    ]

    issues = validate_report_ready_artifacts(resolver, project_id, [declaration], metrics)

    assert "REPORT_READY_GRAIN_MIXED" in {issue["code"] for issue in issues}
    assert "REPORT_READY_MEASURE_GRAIN_MISMATCH" in {
        issue["code"] for issue in issues
    }


def test_dimension_level_single_grain_artifact_is_valid(tmp_path) -> None:
    project_id = "pj_" + "d" * 32
    resolver = PathResolver(tmp_path)
    overall = resolver.resolve(project_id, "data/overall.csv")
    overall.parent.mkdir(parents=True)
    overall.write_text("scope,delivery_rate\noverall,0.9999\n", encoding="utf-8")
    regions = resolver.resolve(project_id, "data/regions.csv")
    regions.write_text("region,late_rate\nAL,0.239\nMA,0.199\n", encoding="utf-8")
    declarations = [
        ReportReadyArtifact.model_validate(
            {
                "artifact_path": "data/regions.csv",
                "fields": [
                    {"name": "region", "role": "dimension"},
                    {"name": "late_rate", "role": "measure", "metric_ref": "late_rate"},
                ],
            }
        ),
    ]
    metrics = [_metric("late_rate", "region", "data/regions.csv")]

    assert validate_report_ready_artifacts(resolver, project_id, declarations, metrics) == []


def test_repeated_scope_label_is_not_a_discriminative_dimension(tmp_path) -> None:
    project_id = "pj_" + "e" * 32
    resolver = PathResolver(tmp_path)
    target = resolver.resolve(project_id, "data/scopes.csv")
    target.parent.mkdir(parents=True)
    target.write_text(
        "scope,late_rate\noverall,0.081\nregion,0.239\nregion,0.199\n",
        encoding="utf-8",
    )
    declaration = ReportReadyArtifact.model_validate(
        {
            "artifact_path": "data/scopes.csv",
            "fields": [
                {"name": "scope", "role": "dimension"},
                {"name": "late_rate", "role": "measure", "metric_ref": "late_rate"},
            ],
        }
    )

    issues = validate_report_ready_artifacts(
        resolver,
        project_id,
        [declaration],
        [_metric("late_rate", "region", "data/scopes.csv")],
    )

    assert "REPORT_READY_DIMENSION_NOT_DISCRIMINATIVE" in {
        issue["code"] for issue in issues
    }


def test_sparse_overall_series_is_incompatible_with_dimension_series(tmp_path) -> None:
    project_id = "pj_" + "f" * 32
    resolver = PathResolver(tmp_path)
    target = resolver.resolve(project_id, "data/coverage.csv")
    target.parent.mkdir(parents=True)
    target.write_text(
        "region,delivery_rate,late_rate\nAL,0.9999,0.239\nMA,,0.199\nCA,,0.179\n",
        encoding="utf-8",
    )
    declaration = ReportReadyArtifact.model_validate(
        {
            "artifact_path": "data/coverage.csv",
            "fields": [
                {"name": "region", "role": "dimension"},
                {"name": "delivery_rate", "role": "measure", "metric_ref": "delivery_rate"},
                {"name": "late_rate", "role": "measure", "metric_ref": "late_rate"},
            ],
        }
    )
    metrics = [
        _metric("delivery_rate", "region", "data/coverage.csv"),
        _metric("late_rate", "region", "data/coverage.csv"),
    ]

    issues = validate_report_ready_artifacts(resolver, project_id, [declaration], metrics)
    codes = {issue["code"] for issue in issues}

    assert "REPORT_READY_MEASURE_COVERAGE_LOW" in codes
    assert "REPORT_READY_SERIES_COVERAGE_INCOMPATIBLE" in codes
