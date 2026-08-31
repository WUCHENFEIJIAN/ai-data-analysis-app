from pathlib import Path

import pytest

from app.sandbox.executor import SandboxExecutor
from app.services.workspace import PathResolver, WorkspaceService


@pytest.mark.asyncio
async def test_real_sandbox_executes_and_isolates_workspace(tmp_path: Path) -> None:
    project_id = "pj_" + "d" * 32
    resolver = PathResolver(tmp_path)
    WorkspaceService(tmp_path).create(project_id)
    resolver.resolve(project_id, "input/source.csv").write_text("value\n1\n2\n", encoding="utf-8")
    script = resolver.resolve(project_id, "scripts/001_sandbox.py")
    script.write_text(
        """from pathlib import Path
import socket
import pandas as pd
import matplotlib.pyplot as plt

root = Path('/workspace')
df = pd.read_csv(root / 'input/source.csv')
df.assign(double=df['value'] * 2).to_csv(root / 'data/result.csv', index=False)
plt.plot(df['value'])
plt.savefig(root / 'charts/result.png')
try:
    socket.create_connection(('1.1.1.1', 80), timeout=0.5)
    network = 'available'
except OSError:
    network = 'blocked'
(root / 'data/network.txt').write_text(network)
(root / 'data/isolation.txt').write_text(str((root / '../other').exists()))
print('rows', len(df))
""",
        encoding="utf-8",
    )
    executor = SandboxExecutor("ai-analysis-sandbox:latest", 20, "1g", 1.0)

    result = await executor.execute(resolver.project_root(project_id), "scripts/001_sandbox.py")

    assert result.status == "success", result.stderr
    assert "rows 2" in result.stdout
    assert resolver.resolve(project_id, "data/result.csv").is_file()
    assert resolver.resolve(project_id, "charts/result.png").stat().st_size > 100
    assert resolver.resolve(project_id, "data/network.txt").read_text() == "blocked"
    assert resolver.resolve(project_id, "data/isolation.txt").read_text() == "False"


@pytest.mark.asyncio
async def test_real_sandbox_times_out_infinite_loop(tmp_path: Path) -> None:
    project_id = "pj_" + "e" * 32
    resolver = PathResolver(tmp_path)
    WorkspaceService(tmp_path).create(project_id)
    resolver.resolve(project_id, "scripts/001_loop.py").write_text("while True: pass\n")
    executor = SandboxExecutor("ai-analysis-sandbox:latest", 1, "256m", 0.5)

    result = await executor.execute(resolver.project_root(project_id), "scripts/001_loop.py")

    assert result.status == "timeout"
    assert result.duration_ms < 10_000
