export type Conversation = {
  id: string;
  title: string;
  active_file_id?: string | null;
  updated_at: string;
};

export type Message = {
  id: string;
  role: string;
  content: string;
  tool_results?: { items?: unknown[] } | null;
  created_at: string;
};

export type UploadedFile = {
  id: string;
  original_name: string;
  sha256: string;
  size_bytes: number;
  analysis: {
    class_name?: string;
    method_count?: number;
    line_count?: number;
    _project_id?: string;
    _project_name?: string;
    _project_build_tool?: string;
    _project_relative_path?: string;
    _source_role?: "production" | "test";
    _is_test_source?: boolean;
    _test_source_reason?: string;
    suggested_test_targets?: Array<{ name: string; parameters?: string; return_type?: string }>;
  };
  created_at: string;
};

export type BatchGenerateResult = {
  ok?: boolean;
  generated_count?: number;
  failed_count?: number;
  skipped_count?: number;
  cancelled?: boolean;
  generated?: Array<{ file_id?: string; file_name?: string; artifact_id?: string; artifact_file?: string }>;
  failed?: Array<{ file_id?: string; file_name?: string; error?: string }>;
  skipped?: Array<{ file_id?: string; name?: string; reason?: string }>;
  [key: string]: unknown;
};

export type Artifact = {
  id: string;
  file_id: string;
  kind: string;
  storage_path: string;
  model: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
};

export type AgentJob = {
  id: string;
  kind: string;
  status: string;
  progress: number;
  stage: string;
  message: string;
  external_id?: string;
  request_json?: Record<string, unknown>;
  result_json?: Record<string, unknown>;
  error?: string;
  cancel_requested?: boolean;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000/api";

function token() {
  return localStorage.getItem("a3_agent_token") || "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (!(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const auth = token();
  if (auth) headers.set("Authorization", `Bearer ${auth}`);
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    if (response.status === 413) {
      throw new Error("上传文件过大。当前反向代理允许最大 1GB，请去掉 target/build/.git 等目录后重新压缩上传。");
    }
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json() as Promise<T>;
}

export async function register(email: string, password: string, displayName: string) {
  return request<{ id: string; email: string }>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, display_name: displayName })
  });
}

export async function login(email: string, password: string) {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form
  });
  if (!response.ok) throw new Error(await response.text());
  const payload = (await response.json()) as { access_token: string };
  localStorage.setItem("a3_agent_token", payload.access_token);
  return payload;
}

export function logout() {
  localStorage.removeItem("a3_agent_token");
}

export function getConversations() {
  return request<Conversation[]>("/chat/conversations");
}

export function createConversation(title = "\u65b0\u5bf9\u8bdd") {
  return request<Conversation>("/chat/conversations", {
    method: "POST",
    body: JSON.stringify({ title })
  });
}

export function deleteConversation(conversationId: string) {
  return request<{ ok: boolean; deleted_conversation_id: string }>(`/chat/conversations/${conversationId}`, {
    method: "DELETE"
  });
}

export function renameConversation(conversationId: string, title: string) {
  return request<Conversation>(`/chat/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ title })
  });
}

export async function exportConversation(conversation: Conversation, format: "markdown" | "json" = "markdown") {
  const response = await fetch(`${API_BASE}/chat/conversations/${conversation.id}/export?format=${encodeURIComponent(format)}`, {
    headers: { Authorization: `Bearer ${token()}` }
  });
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const safeTitle = conversation.title.replace(/[\\/:*?"<>|]+/g, "-").trim() || "conversation";
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${safeTitle}.${format === "json" ? "json" : "md"}`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function getMessages(conversationId: string) {
  return request<Message[]>(`/chat/conversations/${conversationId}/messages`);
}

export function sendChat(message: string, conversationId?: string, activeFileId?: string) {
  return request<{ conversation_id: string; reply: string; tool_results: unknown[] }>("/chat", {
    method: "POST",
    body: JSON.stringify({ message, conversation_id: conversationId, active_file_id: activeFileId })
  });
}

export type StreamHandlers = {
  onMeta?: (payload: Record<string, unknown>) => void;
  onStatus?: (payload: Record<string, unknown>) => void;
  onTool?: (payload: Record<string, unknown>) => void;
  onDelta?: (text: string) => void;
  onDone?: (payload: Record<string, unknown>) => void;
  onError?: (payload: Record<string, unknown>) => void;
};

export async function streamChat(
  message: string,
  conversationId: string | undefined,
  activeFileId: string | undefined,
  handlers: StreamHandlers,
  signal?: AbortSignal
) {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token()}`
    },
    body: JSON.stringify({ message, conversation_id: conversationId, active_file_id: activeFileId })
  });
  if (!response.ok || !response.body) throw new Error(await response.text());
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const raw of events) {
      const event = raw.match(/^event:\s*(.+)$/m)?.[1] || "message";
      const dataLine = raw.match(/^data:\s*(.+)$/m)?.[1] || "{}";
      const payload = JSON.parse(dataLine) as Record<string, unknown>;
      if (event === "meta") handlers.onMeta?.(payload);
      if (event === "status") handlers.onStatus?.(payload);
      if (event === "tool") handlers.onTool?.(payload);
      if (event === "delta") handlers.onDelta?.(String(payload.text || ""));
      if (event === "done") handlers.onDone?.(payload);
      if (event === "error") handlers.onError?.(payload);
    }
  }
}

export type BatchStreamHandlers = {
  onMeta?: (payload: Record<string, unknown>) => void;
  onProgress?: (payload: Record<string, unknown>) => void;
  onItem?: (payload: Record<string, unknown>) => void;
  onDone?: (payload: BatchGenerateResult) => void;
  onCancelled?: (payload: Record<string, unknown>) => void;
  onError?: (payload: Record<string, unknown>) => void;
};

export type JobStreamHandlers = {
  onProgress?: (payload: AgentJob) => void;
  onDone?: (payload: AgentJob) => void;
  onError?: (payload: Record<string, unknown>) => void;
};

export function uploadJava(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<UploadedFile>("/files/upload", { method: "POST", body: form });
}

export function uploadJavaBatch(files: File[]) {
  const form = new FormData();
  files.forEach((file) => {
    form.append("files", file, file.name);
  });
  return request<{ files: UploadedFile[]; rejected: Array<{ name: string; reason: string }> }>("/files/upload/batch", {
    method: "POST",
    body: form
  });
}

export function getFiles() {
  return request<UploadedFile[]>("/files");
}

export function deleteUploadedFile(fileId: string) {
  return request<{ ok: boolean; deleted_file_id: string; deleted_artifacts: number }>(`/files/${fileId}`, {
    method: "DELETE"
  });
}

export function deleteUploadedFiles(fileIds: string[]) {
  return request<{ ok: boolean; deleted_file_ids: string[]; deleted_artifacts: number; not_found: string[] }>("/files/delete/batch", {
    method: "POST",
    body: JSON.stringify({ file_ids: fileIds })
  });
}

export function generateTestsBatch(fileIds?: string[], onlyMissing = true) {
  return request<BatchGenerateResult>("/files/generate/batch", {
    method: "POST",
    body: JSON.stringify({
      file_ids: fileIds && fileIds.length ? fileIds : undefined,
      only_missing: onlyMissing,
      max_files: 200,
      goal: "Generate JUnit 4 tests for all selected Java files."
    })
  });
}

export async function streamGenerateTestsBatch(
  fileIds: string[] | undefined,
  onlyMissing: boolean,
  jobId: string,
  handlers: BatchStreamHandlers,
  signal?: AbortSignal
) {
  const response = await fetch(`${API_BASE}/files/generate/batch/stream`, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token()}`
    },
    body: JSON.stringify({
      file_ids: fileIds && fileIds.length ? fileIds : undefined,
      only_missing: onlyMissing,
      max_files: 200,
      job_id: jobId,
      goal: "Generate JUnit 4 tests for all selected Java files."
    })
  });
  if (!response.ok || !response.body) throw new Error(await response.text());
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const raw of events) {
      const event = raw.match(/^event:\s*(.+)$/m)?.[1] || "message";
      const dataLine = raw.match(/^data:\s*(.+)$/m)?.[1] || "{}";
      const payload = JSON.parse(dataLine) as Record<string, unknown>;
      if (event === "meta") handlers.onMeta?.(payload);
      if (event === "progress") handlers.onProgress?.(payload);
      if (event === "item") handlers.onItem?.(payload);
      if (event === "done") handlers.onDone?.(payload as BatchGenerateResult);
      if (event === "cancelled") handlers.onCancelled?.(payload);
      if (event === "error") handlers.onError?.(payload);
    }
  }
}

export function cancelBatchGenerate(jobId: string) {
  return request<{ ok: boolean; job_id: string; cancelled: boolean }>(`/files/generate/batch/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST"
  });
}

export function extractCodeContext(fileIds?: string[], projectId?: string) {
  return request<AgentJob>("/files/context/extract", {
    method: "POST",
    body: JSON.stringify({
      file_ids: fileIds && fileIds.length ? fileIds : undefined,
      project_id: projectId
    })
  });
}

export async function streamJob(jobId: string, handlers: JobStreamHandlers, signal?: AbortSignal) {
  const response = await fetch(`${API_BASE}/files/jobs/${encodeURIComponent(jobId)}/stream`, {
    method: "GET",
    signal,
    headers: { Authorization: `Bearer ${token()}` }
  });
  if (!response.ok || !response.body) throw new Error(await response.text());
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const raw of events) {
      const event = raw.match(/^event:\s*(.+)$/m)?.[1] || "message";
      const dataLine = raw.match(/^data:\s*(.+)$/m)?.[1] || "{}";
      const payload = JSON.parse(dataLine) as AgentJob;
      if (event === "progress") handlers.onProgress?.(payload);
      if (event === "done") handlers.onDone?.(payload);
      if (event === "error") handlers.onError?.(payload as unknown as Record<string, unknown>);
    }
  }
}

export function cancelJob(jobId: string) {
  return request<{ ok: boolean; job_id: string; cancel_requested: boolean; status: string }>(
    `/files/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" }
  );
}

export function getArtifacts(fileId: string) {
  return request<Artifact[]>(`/files/${fileId}/artifacts`);
}

export async function downloadArtifact(artifact: Artifact) {
  const response = await fetch(`${API_BASE}/files/artifacts/${artifact.id}/download`, {
    headers: { Authorization: `Bearer ${token()}` }
  });
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = artifact.storage_path.split(/[\\/]/).pop() || "GeneratedTest.java";
  anchor.click();
  URL.revokeObjectURL(url);
}

export function readArtifact(artifact: Artifact) {
  return request<{ artifact: Artifact; code: string }>(`/files/artifacts/${artifact.id}`);
}

export async function downloadArtifactsZip(fileId?: string) {
  const suffix = fileId ? `?file_id=${encodeURIComponent(fileId)}` : "";
  const response = await fetch(`${API_BASE}/files/artifacts.zip${suffix}`, {
    headers: { Authorization: `Bearer ${token()}` }
  });
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileId ? `${fileId}-artifacts.zip` : "a3-agent-artifacts.zip";
  anchor.click();
  URL.revokeObjectURL(url);
}

export function rateMessage(messageId: string, rating: "up" | "down", note = "") {
  return request(`/chat/messages/${messageId}/feedback`, {
    method: "POST",
    body: JSON.stringify({ rating, note })
  });
}

export function inspectWorkspace() {
  return request<Record<string, unknown>>("/workspace/inspect");
}
