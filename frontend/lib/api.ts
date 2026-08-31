import type { AnalysisRun, Artifact, FileNode, FilePreview, Message, ModelConfiguration, ModelConfigurationUpdate, ModelConnectionTest, ModelConnectionTestRequest, ModelPresetList, PlanningAction, Project, ProjectList, UploadedFile } from "../types/api";

export function apiBaseUrl(location?: Pick<Location, "protocol" | "hostname">): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (configured) return configured.replace(/\/$/, "");
  const current = location ?? (typeof window === "undefined" ? undefined : window.location);
  if (!current) return "http://localhost:8000/api";
  const isLocal = ["localhost", "127.0.0.1", "0.0.0.0"].includes(current.hostname);
  return `${current.protocol}//${current.hostname}${isLocal ? ":8000" : ""}/api`;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new ApiError(response.status, body?.error?.message ?? "请求失败");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type HealthResponse = { status: "ok"; database: "ok" };
export const getHealth = () => apiRequest<HealthResponse>("/health");

export const listProjects = () => apiRequest<ProjectList>("/projects");
export const createProject = (name: string) =>
  apiRequest<Project>("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
export const getProject = (projectId: string) => apiRequest<Project>(`/projects/${projectId}`);
export const deleteProject = (projectId: string) =>
  apiRequest<void>(`/projects/${projectId}`, { method: "DELETE" });
export const listFiles = (projectId: string) =>
  apiRequest<FileNode[]>(`/projects/${projectId}/files`);
export const previewFile = (projectId: string, path: string) =>
  apiRequest<FilePreview>(`/projects/${projectId}/files/${encodePath(path)}`);
export const listMessages = (projectId: string) =>
  apiRequest<Message[]>(`/projects/${projectId}/messages`);
export const createAnalysisPlan = (projectId: string, message: string) =>
  apiRequest<PlanningAction>(`/projects/${projectId}/analysis/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
export const getLatestAnalysis = (projectId: string) =>
  apiRequest<AnalysisRun | null>(`/projects/${projectId}/analysis`);
export const getAnalysis = (runId: string) => apiRequest<AnalysisRun>(`/analysis/${runId}`);
export const startAnalysis = (projectId: string, message: string) =>
  apiRequest<AnalysisRun>(`/projects/${projectId}/analysis`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
export const resumeAnalysis = (runId: string, message: string) =>
  apiRequest<AnalysisRun>(`/analysis/${runId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
export const stopAnalysis = (runId: string) =>
  apiRequest<AnalysisRun>(`/analysis/${runId}/stop`, { method: "POST" });
export const retryAnalysis = (runId: string) =>
  apiRequest<AnalysisRun>(`/analysis/${runId}/retry`, { method: "POST" });
export const regenerateReport = (runId: string) =>
  apiRequest<AnalysisRun>(`/analysis/${runId}/report`, { method: "POST" });
export const listArtifacts = (projectId: string, artifactType?: string) =>
  apiRequest<Artifact[]>(`/projects/${projectId}/artifacts${artifactType ? `?type=${encodeURIComponent(artifactType)}` : ""}`);
export const listModelPresets = () => apiRequest<ModelPresetList>("/settings/models");
export const getModelConfiguration = () => apiRequest<ModelConfiguration | null>("/settings/model");
export const updateModelConfiguration = (payload: ModelConfigurationUpdate) =>
  apiRequest<ModelConfiguration>("/settings/model", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
export const testModelConnection = (payload: ModelConnectionTestRequest) =>
  apiRequest<ModelConnectionTest>("/settings/model/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

export function absoluteApiUrl(path: string): string {
  return path.startsWith("http") ? path : `${apiBaseUrl().replace(/\/api$/, "")}${path}`;
}

export function fileDownloadUrl(projectId: string, path: string): string {
  return `${apiBaseUrl()}/projects/${projectId}/files/${encodePath(path)}/download`;
}

export function fileContentUrl(projectId: string, path: string): string {
  return `${apiBaseUrl()}/projects/${projectId}/files/${encodePath(path)}/content`;
}

export function analysisEventsUrl(runId: string): string {
  return `${apiBaseUrl()}/analysis/${runId}/events`;
}

export function uploadFile(
  projectId: string,
  file: File,
  onProgress: (progress: number) => void,
): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `${apiBaseUrl()}/projects/${projectId}/files`);
    request.timeout = 5 * 60 * 1000;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () => reject(new ApiError(0, "上传连接失败"));
    request.ontimeout = () => reject(new ApiError(0, "上传处理超时"));
    request.onabort = () => reject(new ApiError(0, "上传已取消"));
    request.onload = () => {
      let body: UploadedFile & { error?: { message?: string } };
      try {
        body = JSON.parse(request.responseText || "{}") as UploadedFile & {
          error?: { message?: string };
        };
      } catch {
        reject(new ApiError(request.status, "上传服务返回了无法读取的响应"));
        return;
      }
      if (request.status >= 200 && request.status < 300) resolve(body);
      else reject(new ApiError(request.status, body.error?.message ?? "上传失败"));
    };
    const form = new FormData();
    form.append("file", file);
    request.send(form);
  });
}

function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}
