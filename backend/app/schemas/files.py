from typing import Any, Literal

from pydantic import BaseModel


class FileNode(BaseModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size_bytes: int | None = None
    children: list["FileNode"] | None = None


class UploadedFile(BaseModel):
    name: str
    path: str
    size_bytes: int
    profile_status: Literal["completed", "failed"] | None = None
    profile_error: str | None = None


class FilePreview(BaseModel):
    path: str
    kind: Literal["text", "json", "csv", "image", "html"]
    size_bytes: int
    revision: str | None = None
    truncated: bool = False
    content: str | dict[str, Any] | list[Any] | None = None
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    download_url: str | None = None
    content_url: str | None = None
