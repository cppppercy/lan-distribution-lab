export type Scope = "read" | "read_write";

export interface SessionInfo {
  expires_at: string;
  scope: Scope;
}

export interface FileRecord {
  file_id: string;
  display_name: string;
  size_bytes: number;
  content_sha256: string;
  etag: string;
  created_at: string;
}

export interface TransferRecord {
  transfer_id: string;
  direction: "upload" | "download";
  state: "started" | "completed" | "failed" | "cancelled";
  bytes_transferred: number;
  size_bytes: number | null;
  error_code: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface UploadResult {
  file: FileRecord;
  transfer: TransferRecord;
}
