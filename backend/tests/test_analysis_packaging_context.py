import json
from types import SimpleNamespace

from app.agent.run_context import RunContextBuilder


def _execution(task_id: str, script_path: str, stdout: str = ""):
    return SimpleNamespace(
        id=f"exec_{task_id}",
        task_id=task_id,
        script_path=script_path,
        status="success",
        exit_code=0,
        stdout=stdout,
        stderr="",
    )


def _write_generated_result(workspace, task_id: str, name: str, result: dict):
    script_path = workspace / "scripts" / f"{task_id}.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        f"output_path = 'data/{name}'\n",
        encoding="utf-8",
    )
    result_path = workspace / "data" / name
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return script_path.relative_to(workspace).as_posix()


def test_packaging_context_keeps_all_completed_plan_topics(tmp_path):
    plan = {
        "tasks": [
            {"id": "task_a", "title": "Topic A", "goal": "Study category_a"},
            {"id": "task_b", "title": "Topic B", "goal": "Study metric_x"},
            {"id": "task_c", "title": "Topic C", "goal": "Study rate_z"},
        ]
    }
    executions = []
    for task_id, name, result in (
        ("task_a", "artifact_a.json", {"category_a": "A"}),
        ("task_b", "artifact_b.json", {"metric_x": 10, "metric_y": 20}),
        ("task_c", "artifact_c.json", {"rate_z": 0.25}),
    ):
        script_path = _write_generated_result(tmp_path, task_id, name, result)
        executions.append(_execution(task_id, script_path))

    context = RunContextBuilder.analysis_packaging_context(tmp_path, plan, executions)

    assert [item["task_id"] for item in context["plan_coverage"]] == [
        "task_a",
        "task_b",
        "task_c",
    ]
    assert all(item["status"] == "completed" for item in context["plan_coverage"])
    assert {item["artifact_path"] for item in context["structured_results"]} == {
        "data/artifact_a.json",
        "data/artifact_b.json",
        "data/artifact_c.json",
    }


def test_packaging_context_does_not_depend_on_latest_stdout_window(tmp_path):
    plan = {
        "tasks": [
            {"id": "task_a", "title": "Early topic", "goal": "Study metric_x"},
            {"id": "task_c", "title": "Latest topic", "goal": "Study metric_y"},
        ]
    }
    early_script = _write_generated_result(
        tmp_path, "task_a", "early_result.json", {"metric_x": 42}
    )
    latest_script = _write_generated_result(
        tmp_path, "task_c", "latest_result.json", {"metric_y": 84}
    )
    executions = [
        _execution("task_c", latest_script, "x" * 5000 + "latest only"),
        _execution("task_a", early_script, "early topic" + "x" * 5000),
    ]

    context = RunContextBuilder.analysis_packaging_context(tmp_path, plan, executions)

    assert context["structured_results"][0]["artifact_path"] == "data/early_result.json"
    assert context["structured_results"][0]["result"]["metric_x"] == 42
    assert {item["task_id"] for item in context["plan_coverage"]} == {"task_a", "task_c"}


def test_packaging_context_is_bounded_and_uses_neutral_schema(tmp_path):
    plan = {
        "tasks": [
            {"id": "task_neutral", "title": "Neutral", "goal": "Study category_a"}
        ]
    }
    script_path = _write_generated_result(
        tmp_path,
        "task_neutral",
        "neutral_result.json",
        {"category_a": "A", "metric_x": 1, "metric_y": 2, "rate_z": 0.5},
    )

    context = RunContextBuilder.analysis_packaging_context(
        tmp_path, plan, [_execution("task_neutral", script_path)]
    )
    serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))

    assert len(serialized) <= RunContextBuilder.PACKAGING_CONTEXT_MAX_CHARS
    assert "category_a" in serialized
    assert "metric_x" in serialized
    assert "rate_z" in serialized


def test_packaging_context_includes_bounded_object_result_without_path_literal(tmp_path):
    plan = {
        "tasks": [
            {"id": "task_neutral", "title": "Neutral", "goal": "Study entity_count"}
        ]
    }
    script = tmp_path / "scripts" / "task_neutral.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "output_dir = workspace / 'data'\nresult_path = output_dir / result_name\n",
        encoding="utf-8",
    )
    result = tmp_path / "data" / "computed_result.json"
    result.parent.mkdir(parents=True)
    result.write_text('{"entity_count":12,"metric_x":3}', encoding="utf-8")

    context = RunContextBuilder.analysis_packaging_context(
        tmp_path,
        plan,
        [_execution("task_neutral", script.relative_to(tmp_path).as_posix())],
    )

    assert context["structured_results"] == [
        {
            "artifact_path": "data/computed_result.json",
            "origin_task_ids": [],
            "result": {"entity_count": 12, "metric_x": 3},
        }
    ]


def test_packaging_context_includes_canonical_available_metrics(tmp_path):
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "metrics.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "metrics": [
                    {
                        "metric_id": "total_amount",
                        "metric_scope": "scalar_evidence",
                        "label": "Total amount",
                        "value": 7,
                        "aggregation": "sum",
                        "semantic_type": "measure",
                        "unit_family": "currency",
                        "definition": "Sum of amount",
                        "source_artifact": "data/summary.json",
                        "source_field": "total_amount",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    context = RunContextBuilder.analysis_packaging_context(tmp_path, None, [])

    assert context["available_metrics"] == [
        {
            "metric_id": "total_amount",
            "metric_scope": "scalar_evidence",
            "label": "Total amount",
            "source_artifact": "data/summary.json",
            "source_field": "total_amount",
            "unit_family": "currency",
        }
    ]
