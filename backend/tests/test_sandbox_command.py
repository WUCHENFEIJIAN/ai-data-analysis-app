from pathlib import Path

from app.sandbox.executor import SandboxExecutor


def test_sandbox_command_enforces_isolation_and_resource_limits(tmp_path: Path) -> None:
    for name in ("input", "context", "plans", "scripts"):
        (tmp_path / name).mkdir()
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "findings.json").write_text("{}")
    executor = SandboxExecutor("analysis:test", 10, "512m", 1.5)
    name, command = executor.build_command("exec_1", tmp_path, "scripts/001_test.py")
    joined = " ".join(str(value) for value in command)

    assert name == "analysis-exec_1"
    assert "--network none" in joined
    assert "--read-only" in command
    assert "/workspace:ro" in joined
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--memory 512m" in joined
    assert "--cpus 1.5" in joined
    assert "--pids-limit 128" in joined
    assert "/workspace/input:ro" in joined
    assert "/workspace/scripts:ro" in joined
    assert "/workspace/analysis:ro" in joined
    assert "/workspace/data:rw" in joined
    assert "/workspace/generated:rw" in joined
    assert "/workspace/charts:rw" in joined
    assert "/workspace/logs:rw" in joined
    assert "/workspace/reports:rw" not in joined
    assert ".env" not in joined
    assert "docker.sock" not in joined
