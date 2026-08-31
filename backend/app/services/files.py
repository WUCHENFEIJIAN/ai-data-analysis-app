import csv
import io
import json
import re
from pathlib import Path

from fastapi import UploadFile

from app.core.errors import NotFoundError, ValidationError
from app.schemas.files import FileNode, FilePreview, UploadedFile
from app.services.workspace import PathResolver

ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xlsx", ".xls"}
TEXT_PREVIEW_EXTENSIONS = {".py", ".txt", ".md"}
IMAGE_EXTENSIONS = {".png", ".svg"}
MAX_TEXT_PREVIEW_BYTES = 200_000
MAX_CSV_PREVIEW_ROWS = 100


def sanitize_filename(filename: str) -> str:
    leaf = Path(filename.replace("\\", "/")).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(leaf).stem).strip("._") or "file"
    suffix = Path(leaf).suffix.lower()
    return f"{stem[:100]}{suffix}"


class FileService:
    def __init__(self, resolver: PathResolver, max_upload_bytes: int) -> None:
        self.resolver = resolver
        self.max_upload_bytes = max_upload_bytes

    async def upload(self, project_id: str, upload: UploadFile) -> UploadedFile:
        safe_name = sanitize_filename(upload.filename or "")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ValidationError("Only .csv, .xlsx, and .xls files are supported")
        target = self._unique_target(project_id, safe_name)
        size = 0
        try:
            with target.open("xb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise ValidationError("Uploaded file exceeds the size limit")
                    destination.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        return UploadedFile(name=target.name, path=f"input/{target.name}", size_bytes=size)

    def tree(self, project_id: str) -> list[FileNode]:
        root = self.resolver.project_root(project_id)
        if not root.exists():
            raise NotFoundError("Project workspace")
        return [
            self._node(path, root) for path in sorted(root.iterdir(), key=lambda item: item.name)
        ]

    def preview(self, project_id: str, relative_path: str) -> FilePreview:
        path = self._existing_file(project_id, relative_path)
        suffix = path.suffix.lower()
        stat = path.stat()
        size = stat.st_size
        if suffix == ".csv":
            return self._preview_csv(path, relative_path, size)
        if suffix == ".json":
            return self._preview_json(path, relative_path, size)
        if suffix in TEXT_PREVIEW_EXTENSIONS:
            return self._preview_text(path, relative_path, size)
        if suffix in IMAGE_EXTENSIONS:
            return FilePreview(
                path=relative_path,
                kind="image",
                size_bytes=size,
                download_url=f"/api/projects/{project_id}/files/{relative_path}/download",
            )
        if suffix == ".html":
            return FilePreview(
                path=relative_path,
                kind="html",
                size_bytes=size,
                revision=f"{stat.st_mtime_ns:x}-{size:x}",
                content_url=f"/api/projects/{project_id}/files/{relative_path}/content",
            )
        raise ValidationError("This file type cannot be previewed")

    def download_path(self, project_id: str, relative_path: str) -> Path:
        return self._existing_file(project_id, relative_path)

    def _unique_target(self, project_id: str, filename: str) -> Path:
        input_directory = self.resolver.resolve(project_id, "input")
        input_directory.mkdir(exist_ok=True)
        candidate = input_directory / filename
        counter = 2
        while candidate.exists():
            candidate = input_directory / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
            counter += 1
        return candidate

    def _existing_file(self, project_id: str, relative_path: str) -> Path:
        path = self.resolver.resolve(project_id, relative_path)
        if not path.is_file():
            raise NotFoundError("File")
        return path

    def _node(self, path: Path, root: Path) -> FileNode:
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            children = [
                self._node(child, root)
                for child in sorted(path.iterdir(), key=lambda item: item.name)
            ]
            return FileNode(name=path.name, path=relative, kind="directory", children=children)
        return FileNode(name=path.name, path=relative, kind="file", size_bytes=path.stat().st_size)

    def _preview_text(self, path: Path, relative_path: str, size: int) -> FilePreview:
        raw = path.read_bytes()[:MAX_TEXT_PREVIEW_BYTES]
        return FilePreview(
            path=relative_path,
            kind="text",
            size_bytes=size,
            truncated=size > len(raw),
            content=raw.decode("utf-8", errors="replace"),
        )

    def _preview_json(self, path: Path, relative_path: str, size: int) -> FilePreview:
        if size > MAX_TEXT_PREVIEW_BYTES:
            raise ValidationError("JSON file is too large to preview")
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("JSON file cannot be read") from exc
        return FilePreview(path=relative_path, kind="json", size_bytes=size, content=content)

    def _preview_csv(self, path: Path, relative_path: str, size: int) -> FilePreview:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
            sample = source.read(MAX_TEXT_PREVIEW_BYTES)
        reader = csv.reader(io.StringIO(sample))
        rows = []
        for index, row in enumerate(reader):
            if index > MAX_CSV_PREVIEW_ROWS:
                break
            rows.append([value[:500] for value in row])
        columns = rows[0] if rows else []
        data_rows = rows[1:]
        return FilePreview(
            path=relative_path,
            kind="csv",
            size_bytes=size,
            truncated=size > len(sample.encode("utf-8")) or len(data_rows) >= MAX_CSV_PREVIEW_ROWS,
            columns=columns,
            rows=data_rows[:MAX_CSV_PREVIEW_ROWS],
        )
