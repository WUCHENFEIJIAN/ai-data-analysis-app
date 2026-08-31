export interface Project {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectList {
  items: Project[];
  total: number;
}

export interface FileNode {
  name: string;
  path: string;
  kind: "file" | "directory";
  size_bytes?: number;
  children?: FileNode[];
}

export interface UploadedFile {
  name: string;
  path: string;
  size_bytes: number;
  profile_status?: "completed" | "failed";
  profile_error?: string;
}

export interface FilePreview {
  path: string;
  kind: "text" | "json" | "csv" | "image" | "html";
  size_bytes: number;
  revision?: string;
  truncated: boolean;
  content?: unknown;
  columns?: string[];
  rows?: string[][];
  download_url?: string;
  content_url?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  message_type: "text" | "plan" | "question" | "result" | "error";
  created_at: string;
}

export interface PlanTask {
  id: string;
  title: string;
  goal: string;
  sequence: number;
}

export type PlanningAction =
  | { action: "ask_user"; question: string; reason: string }
  | { action: "create_plan"; title: string; objective: string; tasks: PlanTask[] };

export type AnalysisStatus = "pending" | "running" | "waiting_user" | "completed" | "failed" | "stopped";

export interface AnalysisRun {
  id: string;
  project_id: string;
  user_request: string;
  status: AnalysisStatus;
  state: string;
  step_count: number;
  execution_count: number;
  code_retry_count: number;
  cancellation_requested: boolean;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Artifact {
  id: string;
  project_id: string;
  path: string;
  artifact_type: "script" | "data" | "chart" | "analysis" | "context" | "plan" | "report" | "report_spec" | "file";
  size_bytes: number;
  created_at: string;
}

export interface AnalysisEvent {
  event: string;
  run_id: string;
  data: Record<string, unknown>;
  sequence: number;
}

export type ThemeMode = "light" | "dark" | "system";

export interface ModelPreset {
  id: string;
  label: string;
  description: string;
  provider: "openai_compatible" | "anthropic";
  api_base: string;
  model: string;
  requires_api_base: boolean;
}

export interface ModelPresetList {
  items: ModelPreset[];
}

export interface ModelConfiguration {
  preset_id: string;
  provider: "openai_compatible" | "anthropic";
  display_name: string;
  api_base: string;
  model: string;
  api_key_configured: boolean;
  api_key_hint?: string | null;
  updated_at?: string | null;
}

export interface ModelConfigurationUpdate {
  preset_id: string;
  api_base?: string;
  api_key?: string;
  model?: string;
  display_name?: string;
  clear_api_key?: boolean;
}

export interface ModelConnectionTestRequest {
  preset_id: string;
  api_base?: string;
  api_key?: string;
  model?: string;
}

export interface ModelConnectionTest {
  ok: boolean;
  provider: "openai_compatible" | "anthropic";
  model: string;
  latency_ms: number;
  message: string;
}
