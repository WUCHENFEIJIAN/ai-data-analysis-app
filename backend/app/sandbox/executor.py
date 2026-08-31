import asyncio
import re
import time
import uuid
from pathlib import Path

from app.schemas.execution import ExecutionResult

OUTPUT_LIMIT_BYTES = 50_000
READ_ONLY_WORKSPACE_DIRECTORIES = ("input", "context", "plans", "scripts", "analysis")
WRITABLE_WORKSPACE_DIRECTORIES = ("data", "generated", "charts", "logs")


class SandboxExecutor:
    def __init__(
        self,
        image: str,
        timeout_seconds: int,
        memory_limit: str,
        cpu_limit: float,
    ) -> None:
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self._containers: dict[str, str] = {}

    def build_command(
        self, execution_id: str, workspace: Path, script_path: str
    ) -> tuple[str, list[str]]:
        container_name = f"analysis-{re.sub(r'[^A-Za-z0-9_.-]', '-', execution_id)}"
        workspace = workspace.resolve()
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--cpus",
            str(self.cpu_limit),
            "--memory",
            self.memory_limit,
            "--pids-limit",
            "128",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65532:65532",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            "--env",
            "HOME=/tmp",
            "--env",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "--volume",
            f"{workspace}:/workspace:ro",
        ]
        for directory in READ_ONLY_WORKSPACE_DIRECTORIES:
            command.extend(["--volume", f"{workspace / directory}:/workspace/{directory}:ro"])
        for directory in WRITABLE_WORKSPACE_DIRECTORIES:
            command.extend(["--volume", f"{workspace / directory}:/workspace/{directory}:rw"])
        command.extend(
            ["--workdir", "/workspace", self.image, "python", f"/workspace/{script_path}"]
        )
        return container_name, command

    async def execute(
        self,
        workspace: Path,
        script_path: str,
        execution_id: str | None = None,
    ) -> ExecutionResult:
        execution_id = execution_id or f"exec_{uuid.uuid4().hex}"
        for directory in (*READ_ONLY_WORKSPACE_DIRECTORIES, *WRITABLE_WORKSPACE_DIRECTORIES):
            (workspace / directory).mkdir(exist_ok=True)
        container_name, command = self.build_command(execution_id, workspace, script_path)
        self._containers[execution_id] = container_name
        started = time.perf_counter()
        logs_directory = workspace / "logs"
        logs_directory.mkdir(exist_ok=True)
        stdout_path = logs_directory / f"{execution_id}.stdout.log"
        stderr_path = logs_directory / f"{execution_id}.stderr.log"
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            self._containers.pop(execution_id, None)
            return ExecutionResult(
                execution_id=execution_id,
                status="failed",
                exit_code=None,
                stdout="",
                stderr=f"Docker runtime is unavailable: {type(exc).__name__}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                script_path=script_path,
            )
        stdout_task = asyncio.create_task(self._capture(process.stdout, stdout_path))
        stderr_task = asyncio.create_task(self._capture(process.stderr, stderr_path))
        status = "failed"
        try:
            await asyncio.wait_for(process.wait(), timeout=self.timeout_seconds)
            status = "success" if process.returncode == 0 else "failed"
        except TimeoutError:
            status = "timeout"
            await self._kill_container(container_name)
            await process.wait()
        finally:
            self._containers.pop(execution_id, None)
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        logs = []
        if stdout_truncated:
            logs.append(f"logs/{stdout_path.name}")
        else:
            stdout_path.unlink(missing_ok=True)
        if stderr_truncated:
            logs.append(f"logs/{stderr_path.name}")
        else:
            stderr_path.unlink(missing_ok=True)
        return ExecutionResult(
            execution_id=execution_id,
            status=status,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
            script_path=script_path,
            logs=logs,
        )

    async def stop(self, execution_id: str) -> bool:
        container_name = self._containers.get(execution_id)
        if not container_name:
            return False
        await self._kill_container(container_name)
        return True

    @staticmethod
    async def _capture(stream: asyncio.StreamReader | None, log_path: Path) -> tuple[str, bool]:
        captured = bytearray()
        total = 0
        with log_path.open("wb") as log_file:
            if stream is not None:
                while chunk := await stream.read(8192):
                    log_file.write(chunk)
                    total += len(chunk)
                    if len(captured) < OUTPUT_LIMIT_BYTES:
                        captured.extend(chunk[: OUTPUT_LIMIT_BYTES - len(captured)])
        text = captured.decode("utf-8", errors="replace")
        if total > OUTPUT_LIMIT_BYTES:
            text += "\n[output truncated]"
        return text, total > OUTPUT_LIMIT_BYTES

    @staticmethod
    async def _kill_container(container_name: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "kill",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
