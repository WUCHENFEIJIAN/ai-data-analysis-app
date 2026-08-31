import json

from app.agent.run_context import RunContextBuilder
from app.models import Execution
from app.services.analysis_runs import AnalysisRunService
from app.services.artifacts import ArtifactService
from app.services.python_failure import (
    dataframe_dependency_issues,
    dataframe_truthiness_issues,
    parse_python_failure,
    preflight_failure,
    readonly_workspace_write_issues,
)
from app.services.workspace import PathResolver


def test_failure_identity_includes_real_line_and_source_excerpt() -> None:
    code = 'import pandas as pd\ndf = pd.DataFrame({"date": [1]})\nprint(df["日期"])\n'
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "/workspace/scripts/004_prepare.py", line 3, in <module>\n'
        '    print(df["日期"])\n'
        "KeyError: 日期\n"
    )

    failure = parse_python_failure(stderr, "scripts/004_prepare.py", code)

    assert failure.exception_type == "KeyError"
    assert failure.line == 3
    assert 'df["日期"]' in failure.failing_line
    assert "3: print" in failure.code_excerpt
    assert failure.semantic_fingerprint == "KeyError|日期"


def test_dependency_hints_include_all_downstream_columns_from_same_projection() -> None:
    code = """\
fields = ["entity_id", "base_value"]
entities = source[fields].copy()
entities["metric_x"] = entities["base_value"] * 2
by_group = entities.groupby("group_a").agg(total=("metric_x", "sum"))
by_subgroup = entities.groupby(["group_a", "group_b"]).size()
"""

    issues = dataframe_dependency_issues(code)

    assert len(issues) == 1
    assert issues[0].dataframe == "entities"
    assert issues[0].missing_columns == ("group_a", "group_b")
    assert issues[0].downstream_references == (("group_a", 4), ("group_b", 5))


def test_dependency_hints_respect_derived_columns_and_reassignment() -> None:
    code = """\
entities = source[["entity_id", "base_value"]].copy()
entities["metric_x"] = entities["base_value"] * 2
summary = entities.groupby("metric_x").size()
entities = load_another_frame()
other = entities.groupby("group_a").size()
"""

    assert dataframe_dependency_issues(code) == []


def test_dependency_hints_do_not_treat_columns_added_by_merge_as_projection_gaps() -> None:
    code = """\
left = source[["entity_id", "base_value"]].copy()
right = source[["entity_id", "group_a"]].copy()
summary = left.merge(right, on="entity_id").groupby("group_a").agg(
    total=("base_value", "sum")
)
"""

    assert dataframe_dependency_issues(code) == []


def test_dataframe_dependency_preflight_is_opt_in_for_repair_scripts() -> None:
    code = """\
fields = ["entity_id"]
entities = source[fields].copy()
summary = entities.groupby(["group_a", "group_b"]).size()
"""

    assert preflight_failure(code, "scripts/001_analysis.py") is None
    failure = preflight_failure(
        code,
        "scripts/002_analysis_repaired.py",
        check_dataframe_dependencies=True,
    )

    assert failure is not None
    assert failure.exception_type == "PythonSchemaDependencyError"
    assert failure.source == "preflight"
    assert failure.line == 3
    assert "group_a (line 3)" in failure.message
    assert "group_b (line 3)" in failure.message


def test_repair_context_contains_failed_source_and_real_intermediate_schema(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "Repair context"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "日期,指标\n2026-01-01,1\n", "text/csv")},
    )
    resolver = PathResolver(settings.workspace_root)
    script_path = resolver.resolve(project["id"], "scripts/004_prepare.py")
    source = """\
import pandas as pd
df = pd.read_csv("data/processed.csv")
selected = df[["date"]].copy()
print(selected["metric_x"])
"""
    script_path.write_text(source, encoding="utf-8")
    prior_path = resolver.resolve(project["id"], "scripts/003_prepare.py")
    prior_source = """\
import pandas as pd
df = pd.read_csv("data/processed.csv")
print(df["旧字段"])
"""
    prior_path.write_text(prior_source, encoding="utf-8")
    data_path = resolver.resolve(project["id"], "data/processed.csv")
    data_path.write_text("date,metric_x\n2026-01-01,1\n", encoding="utf-8")
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        ArtifactService(session).register(
            project["id"], "data/processed.csv", data_path.stat().st_size
        )
        execution = Execution(
            id="exec_failed",
            run_id=run.id,
            task_id="task_prepare",
            script_path="scripts/004_prepare.py",
            status="failed",
            exit_code=1,
            stdout="",
            stderr=(
                "Traceback (most recent call last):\n"
                '  File "/workspace/scripts/004_prepare.py", line 4, in <module>\n'
                "KeyError: metric_x\n"
            ),
            duration_ms=1,
        )
        prior_execution = Execution(
            id="exec_prior_failed",
            run_id=run.id,
            task_id="task_prepare",
            script_path="scripts/003_prepare.py",
            status="failed",
            exit_code=1,
            stdout="",
            stderr=(
                "Traceback (most recent call last):\n"
                '  File "/workspace/scripts/003_prepare.py", line 3, in <module>\n'
                "KeyError: 旧字段\n"
            ),
            duration_ms=1,
        )
        session.add(prior_execution)
        session.add(execution)
        session.flush()
        context = RunContextBuilder().build(
            run,
            "skill",
            {
                "files": [
                    {
                        "path": "input/source.xlsx",
                        "sheets": [
                            {
                                "name": "Data",
                                "columns": [
                                    {"name": "entity_id"},
                                    {"name": "group_a"},
                                    {"name": "group_b"},
                                ],
                            }
                        ],
                    }
                ]
            },
            resolver.project_root(project["id"]),
            {"objective": "prepare evidence", "tasks": []},
            execution,
            [execution, prior_execution],
        )

    state = json.loads(context[-1]["content"].splitlines()[1])
    repair = state["python_repair"]
    assert repair["failed_source"] == source
    assert repair["failure"]["line"] == 4
    artifact = next(
        item for item in repair["input_artifacts"] if item["artifact_path"] == "data/processed.csv"
    )
    assert artifact["referenced_by_script"] is True
    assert [column["name"] for column in artifact["schema"]["columns"]] == [
        "date",
        "metric_x",
    ]
    assert repair["known_input_schemas"] == [
        {
            "path": "input/source.xlsx",
            "sheet": "Data",
            "columns": ["entity_id", "group_a", "group_b"],
        }
    ]
    assert repair["dataframe_dependency_hints"] == [
        {
            "dataframe": "selected",
            "producer_line": 3,
            "projected_columns": ["date"],
            "missing_columns": ["metric_x"],
            "downstream_references": [{"column": "metric_x", "line": 4}],
        }
    ]
    assert "minimum complete root-cause repair" in repair["instruction"]
    assert "audit every explicit downstream column reference" in repair["instruction"]
    assert repair["previous_failures"][0]["exception_type"] == "KeyError"
    assert repair["previous_failures"][0]["message"] == "旧字段"
    assert repair["previous_failures"][0]["line"] == 3


def test_readonly_workspace_write_preflight_rejects_analysis_temp_files() -> None:
    code = """\
from pathlib import Path
import tempfile
analysis = Path("analysis")
fd, temp_path = tempfile.mkstemp(dir=str(analysis), suffix=".tmp")
"""

    issues = readonly_workspace_write_issues(code)
    assert issues[0].path == "analysis"
    failure = preflight_failure(code, "scripts/001_analysis.py")
    assert failure is not None
    assert failure.exception_type == "ReadOnlyWorkspaceWriteError"
    assert failure.line == 4
    assert "data/, charts/, or generated/" in failure.message


def test_readonly_workspace_write_preflight_allows_generated_outputs() -> None:
    code = """\
from pathlib import Path
import tempfile
generated = Path("generated")
fd, temp_path = tempfile.mkstemp(dir=str(generated), suffix=".tmp")
"""

    assert readonly_workspace_write_issues(code) == []
    assert preflight_failure(code, "scripts/001_analysis.py") is None


def test_dataframe_truthiness_preflight_rejects_optional_dataframe_condition() -> None:
    code = """\
import pandas as pd
df = pd.read_excel("input/source.xlsx")
def merge_extra(base, extra=None):
    if extra:
        return base.merge(extra)
    return base
result = merge_extra(df, df[["value"]])
"""

    issues = dataframe_truthiness_issues(code)
    assert issues == [type(issues[0])("extra", 4)]
    failure = preflight_failure(code, "scripts/002_analysis.py")
    assert failure is not None
    assert failure.exception_type == "DataFrameTruthinessError"
    assert failure.line == 4
    assert ".empty for DataFrame/Series emptiness" in failure.message


def test_dataframe_truthiness_preflight_allows_explicit_dataframe_checks() -> None:
    code = """\
import pandas as pd
df = pd.read_excel("input/source.xlsx")
def merge_extra(base, extra=None):
    if extra is not None and not extra.empty:
        return base.merge(extra)
    return base
result = merge_extra(df, df[["value"]])
"""

    assert dataframe_truthiness_issues(code) == []
    assert preflight_failure(code, "scripts/002_analysis.py") is None
