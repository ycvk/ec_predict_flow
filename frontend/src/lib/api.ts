import type {
  PipelineRunRequest,
  PipelineRunResponse,
  PipelineTemplateCreateRequest,
  PipelineTemplateResponse,
  PipelineTemplateUpdateRequest,
  RunResponse,
  RunSummaryResponse,
} from "@/lib/types";

const API_V2_PREFIX = "/api/v2";

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init.headers || {}),
    },
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      }
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  // 204/empty body
  const text = await response.text();
  return (text ? (JSON.parse(text) as T) : (undefined as T));
}

export const api = {
  listRuns: async (limit = 20, offset = 0): Promise<RunResponse[]> => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return apiFetch<RunResponse[]>(`${API_V2_PREFIX}/runs?${params.toString()}`, { method: "GET" });
  },

  getRunSummary: async (runId: string): Promise<RunSummaryResponse> => {
    return apiFetch<RunSummaryResponse>(`${API_V2_PREFIX}/runs/${runId}/summary`, { method: "GET" });
  },

  cancelRun: async (runId: string): Promise<{ status: string }> => {
    return apiFetch<{ status: string }>(`${API_V2_PREFIX}/runs/${runId}/cancel`, { method: "POST" });
  },

  runPipeline: async (request: PipelineRunRequest): Promise<PipelineRunResponse> => {
    return apiFetch<PipelineRunResponse>(`${API_V2_PREFIX}/pipelines/run`, {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  listTemplates: async (limit = 200, offset = 0): Promise<PipelineTemplateResponse[]> => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return apiFetch<PipelineTemplateResponse[]>(`${API_V2_PREFIX}/pipeline-templates?${params.toString()}`, {
      method: "GET",
    });
  },

  createTemplate: async (request: PipelineTemplateCreateRequest): Promise<PipelineTemplateResponse> => {
    return apiFetch<PipelineTemplateResponse>(`${API_V2_PREFIX}/pipeline-templates`, {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  updateTemplate: async (
    templateId: string,
    request: PipelineTemplateUpdateRequest,
  ): Promise<PipelineTemplateResponse> => {
    return apiFetch<PipelineTemplateResponse>(`${API_V2_PREFIX}/pipeline-templates/${templateId}`, {
      method: "PUT",
      body: JSON.stringify(request),
    });
  },

  deleteTemplate: async (templateId: string): Promise<{ status: string }> => {
    return apiFetch<{ status: string }>(`${API_V2_PREFIX}/pipeline-templates/${templateId}`, {
      method: "DELETE",
    });
  },

  setDefaultTemplate: async (templateId: string): Promise<{ status: string }> => {
    return apiFetch<{ status: string }>(`${API_V2_PREFIX}/pipeline-templates/${templateId}/set-default`, {
      method: "POST",
    });
  },

  artifactDownloadUrl: (artifactId: string): string => {
    return `${API_V2_PREFIX}/artifacts/${artifactId}/download`;
  },
};

