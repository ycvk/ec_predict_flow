export type RunStatus = "created" | "queued" | "running" | "succeeded" | "failed" | "canceled" | string;
export type StepStatus =
  | "pending"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "canceled"
  | string;

export type ArtifactKind = string;

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | { [key: string]: JsonValue }
  | JsonValue[];

export interface RunResponse {
  run_id: string;
  workflow_name: string;
  step_name: string;
  status: RunStatus;
  params: Record<string, JsonValue>;
  error?: Record<string, JsonValue> | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface StepResponse {
  step_id: string;
  run_id: string;
  name: string;
  status: StepStatus;
  progress: number;
  message?: string | null;
  error?: Record<string, JsonValue> | null;
  queue_task_id?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ArtifactResponse {
  artifact_id: string;
  run_id: string;
  step_id?: string | null;
  kind: ArtifactKind;
  uri: string;
  sha256?: string | null;
  bytes?: number | null;
  metadata: Record<string, JsonValue>;
  created_at: string;
}

export interface RunSummaryResponse {
  run: RunResponse;
  steps: StepResponse[];
  artifacts: ArtifactResponse[];
  summary: Record<string, JsonValue>;
}

export interface PipelineRunRequest {
  workflow_name?: string;
  template_id?: string | null;
  symbol: string;
  start_date: string;
  end_date: string;
  interval: string;
  config_overrides?: Record<string, JsonValue>;
}

export interface PipelineRunResponse {
  run_id: string;
  step_id: string;
  status: string;
  queue_task_id?: string | null;
}

export interface PipelineTemplateResponse {
  template_id: string;
  name: string;
  config: Record<string, JsonValue>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface PipelineTemplateCreateRequest {
  name: string;
  config?: Record<string, JsonValue>;
  is_default?: boolean;
}

export interface PipelineTemplateUpdateRequest {
  name?: string;
  config?: Record<string, JsonValue>;
  is_default?: boolean;
}

