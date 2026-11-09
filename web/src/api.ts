import type { FileRecord, SessionInfo, UploadResult } from "./types";

export class ApiFailure extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
  }
}

interface ErrorPayload {
  error?: { code?: string; message?: string };
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiFailure(0, "network_error", "无法连接到本机服务，请检查网络和服务状态。");
  }
  if (response.status === 204) return undefined as T;
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ErrorPayload;
    throw new ApiFailure(
      response.status,
      payload.error?.code ?? "request_failed",
      payload.error?.message ?? `请求失败（HTTP ${response.status}）。`,
    );
  }
  return (await response.json()) as T;
}

export function pair(pairingCode: string): Promise<{ session: SessionInfo }> {
  return apiFetch("/api/pairings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pairing_code: pairingCode, client_kind: "browser" }),
  });
}

export function listFiles(): Promise<{ items: FileRecord[]; next_cursor: null }> {
  return apiFetch("/api/files");
}

export function revokeSession(): Promise<void> {
  return apiFetch("/api/sessions/current", { method: "DELETE" });
}

export function uploadFile(file: File, onProgress: (loaded: number, total: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append("file", file, file.name);
    const request = new XMLHttpRequest();
    request.open("POST", "/api/uploads");
    request.withCredentials = true;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    request.onerror = () => reject(new ApiFailure(0, "network_error", "上传连接中断。"));
    request.onload = () => {
      const payload = safeJson(request.responseText);
      if (request.status < 200 || request.status >= 300) {
        const error = payload as ErrorPayload;
        reject(new ApiFailure(request.status, error.error?.code ?? "upload_failed", error.error?.message ?? "上传失败。"));
        return;
      }
      resolve(payload as UploadResult);
    };
    request.send(body);
  });
}

export function startDownload(file: FileRecord): void {
  // The browser owns save-dialog and completion semantics; do not report this as saved.
  window.location.assign(`/api/files/${encodeURIComponent(file.file_id)}/content`);
}

function safeJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}
