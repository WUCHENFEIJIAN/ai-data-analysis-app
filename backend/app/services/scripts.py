import re
from pathlib import Path

from app.core.errors import ValidationError
from app.services.workspace import PathResolver


class ScriptManager:
    def __init__(self, resolver: PathResolver) -> None:
        self.resolver = resolver

    def save(self, project_id: str, suggested_name: str, code: str) -> str:
        if not code.strip():
            raise ValidationError("Python code cannot be empty")
        scripts_directory = self.resolver.resolve(project_id, "scripts")
        sequence = self._next_sequence(scripts_directory)
        stem = self._safe_stem(suggested_name)
        filename = f"{sequence:03d}_{stem}.py"
        target = self.resolver.resolve(project_id, f"scripts/{filename}")
        target.write_text(code, encoding="utf-8", newline="\n")
        return f"scripts/{filename}"

    @staticmethod
    def _next_sequence(scripts_directory: Path) -> int:
        numbers = [
            int(match.group(1))
            for path in scripts_directory.glob("*.py")
            if (match := re.match(r"^(\d{3})_", path.name))
        ]
        return max(numbers, default=0) + 1

    @staticmethod
    def _safe_stem(suggested_name: str) -> str:
        leaf = Path(suggested_name.replace("\\", "/")).name
        stem = Path(leaf).stem
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_-").lower()
        if not stem:
            stem = "analysis"
        if stem.startswith(".") or stem in {"env", "dockerfile", "dataset_profile"}:
            stem = f"analysis_{stem.lstrip('.')}"
        return stem[:80]
