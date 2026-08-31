from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.errors import ValidationError
from app.schemas.files import FileNode, FilePreview, UploadedFile
from app.services.dataset_profiler import DatasetProfiler
from app.services.files import FileService
from app.services.projects import ProjectService
from app.services.workspace import PathResolver, WorkspaceService

router = APIRouter(prefix="/projects/{project_id}/files", tags=["files"])


def service(request: Request) -> FileService:
    settings = request.app.state.settings
    return FileService(PathResolver(settings.workspace_root), settings.max_upload_bytes)


def ensure_project(request: Request, project_id: str) -> None:
    with request.app.state.database.session() as session:
        ProjectService(session, WorkspaceService(request.app.state.settings.workspace_root)).get(
            project_id
        )


@router.post("", response_model=UploadedFile, status_code=201)
async def upload_file(
    project_id: str, request: Request, file: Annotated[UploadFile, File()]
) -> UploadedFile:
    ensure_project(request, project_id)
    uploaded = await service(request).upload(project_id, file)
    profile = DatasetProfiler(
        PathResolver(request.app.state.settings.workspace_root)
    ).profile_project(project_id)
    error = next((item for item in profile.errors if item.path == uploaded.path), None)
    uploaded.profile_status = "failed" if error else "completed"
    uploaded.profile_error = error.message if error else None
    return uploaded


@router.get("", response_model=list[FileNode])
def list_files(project_id: str, request: Request) -> list[FileNode]:
    ensure_project(request, project_id)
    return service(request).tree(project_id)


@router.get("/{path:path}/download", response_class=FileResponse)
def download_file(project_id: str, path: str, request: Request) -> FileResponse:
    ensure_project(request, project_id)
    resolved = service(request).download_path(project_id, path)
    return FileResponse(resolved, filename=resolved.name, content_disposition_type="attachment")


@router.get("/{path:path}/content", response_class=FileResponse)
def file_content(project_id: str, path: str, request: Request) -> FileResponse:
    ensure_project(request, project_id)
    resolved = service(request).download_path(project_id, path)
    if resolved.suffix.lower() not in {".html", ".png", ".svg"}:
        raise ValidationError("This file type cannot be served inline")
    headers = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    if resolved.suffix.lower() == ".html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; font-src data:; connect-src 'none'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; "
            "navigate-to 'none'; "
            f"frame-ancestors 'self' {' '.join(request.app.state.settings.frontend_origins)}"
        )
    return FileResponse(resolved, content_disposition_type="inline", headers=headers)


@router.get("/{path:path}", response_model=FilePreview)
def preview_file(project_id: str, path: str, request: Request) -> FilePreview:
    ensure_project(request, project_id)
    preview = service(request).preview(project_id, path)
    if preview.download_url:
        preview.download_url = quote(preview.download_url, safe="/:?=&")
    return preview
