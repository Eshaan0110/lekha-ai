// Typed client for the Lekha API (/api/v1, proxied by Vite). Every call names the signed-in
// synthetic user; the server decides what that user may see.

import { parseSse } from "./sse";

export interface Citation {
  number: number;
  document_id: string;
  version_id: string;
  version_number: number;
  title: string;
  heading_path: string[];
  chunk_id: string;
  span: string;
  kind: "document" | "meeting";
  start_ms: number | null;
}

export interface AskResult {
  query_id: string;
  answer: string;
  abstained: boolean;
  abstention_reason: string | null;
  citations: Citation[];
  language: string;
  model: string | null;
  also_searched: string[];
}

export interface GlossaryEntry {
  id: string;
  canonical: string;
  aliases: string[];
  description: string;
  business_unit_id: string;
  shared: boolean;
  can_edit: boolean;
}

export interface Passage {
  chunk_id: string;
  document_id: string;
  version_number: number;
  title: string;
  heading_path: string[];
  language: string;
  text: string;
  kind: "document" | "meeting";
  start_ms: number | null;
}

export interface MeetingItem {
  id: string;
  document_id: string;
  title: string;
  started_at: string;
  version_number: number;
  participants: string[];
}

export interface MeetingDetail extends MeetingItem {
  transcript: { at: string | null; start_ms: number | null; speaker: string | null; text: string }[];
  you_organised: boolean;
}

export interface Minutes {
  id: string;
  status: "draft" | "approved" | "rejected" | "superseded";
  summary: string;
  decisions: { text: string; evidence: string; at: string | null }[];
  action_items: {
    task: string;
    owner: string;
    due: string;
    evidence: string;
    at: string | null;
    owner_spoke: boolean | null;
  }[];
  dropped_items: number;
  model: string;
  edited: boolean;
  current: boolean;
  created_at: string;
}

export interface Me {
  user_id: string;
  display_name: string;
  business_units: { business_unit_id: string; roles: string[]; clearance: string }[];
}

export interface DocumentItem {
  id: string;
  business_unit_id: string;
  title: string;
  classification: string;
  version_number: number;
}

export interface Job {
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  error_code: string | null;
  chunk_count: number | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(user: string, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "X-Dev-User": `dev|${user}`, ...(init.headers ?? {}) },
  });
  if (!response.ok) throw await toError(response);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

async function toError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } };
    return new ApiError(response.status, body.error?.code ?? "error", body.error?.message ?? "Request failed.");
  } catch {
    return new ApiError(response.status, "error", "Request failed.");
  }
}

export const api = {
  me: (user: string) => call<Me>(user, "/me"),

  documents: async (user: string): Promise<DocumentItem[]> => {
    const items: DocumentItem[] = [];
    let cursor: string | null = null;
    do {
      const query: string = cursor ? `?limit=100&cursor=${encodeURIComponent(cursor)}` : "?limit=100";
      const page: { items: DocumentItem[]; next_cursor: string | null } = await call(user, `/documents${query}`);
      items.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return items;
  },

  passage: (user: string, chunkId: string) => call<Passage>(user, `/chunks/${chunkId}`),

  feedback: (user: string, queryId: string, helpful: boolean, comment?: string) =>
    call<void>(user, `/answers/${queryId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ helpful, comment: comment || null }),
    }),

  upload: (user: string, file: File, metadata: Record<string, unknown>) => {
    const form = new FormData();
    form.append("file", file);
    form.append("metadata", JSON.stringify(metadata));
    return call<{ document_id: string; version_number: number; ingestion_job_id: string }>(user, "/documents", {
      method: "POST",
      body: form,
    });
  },

  job: (user: string, jobId: string) => call<Job>(user, `/ingestion-jobs/${jobId}`),

  meetings: (user: string) => call<MeetingItem[]>(user, "/meetings"),

  meeting: (user: string, id: string) => call<MeetingDetail>(user, `/meetings/${id}`),

  glossary: (user: string) => call<GlossaryEntry[]>(user, "/glossary"),

  addGlossary: (user: string, entry: Record<string, unknown>) =>
    call<GlossaryEntry>(user, "/glossary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entry),
    }),

  deleteGlossary: (user: string, id: string) => call<void>(user, `/glossary/${id}`, { method: "DELETE" }),

  minutes: (user: string, meetingId: string) => call<Minutes[]>(user, `/meetings/${meetingId}/minutes`),

  draftMinutes: (user: string, meetingId: string) =>
    call<Minutes>(user, `/meetings/${meetingId}/minutes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: "standard" }),
    }),

  decideMinutes: (user: string, minutesId: string, approve: boolean) =>
    call<Minutes>(user, `/minutes/${minutesId}/${approve ? "approve" : "reject"}`, { method: "POST" }),

  importMeeting: (user: string, file: File, metadata: Record<string, unknown>) => {
    const form = new FormData();
    form.append("file", file);
    form.append("metadata", JSON.stringify(metadata));
    return call<{ meeting_id: string; version_number: number; ingestion_job_id: string; turns: number }>(
      user,
      "/meetings",
      { method: "POST", body: form },
    );
  },

  /** Ask with live progress. Resolves with the verified answer. */
  async ask(user: string, question: string, onStage: (stage: string, data: Record<string, unknown>) => void): Promise<AskResult> {
    const response = await fetch("/api/v1/ask/stream", {
      method: "POST",
      headers: { "X-Dev-User": `dev|${user}`, "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!response.ok || !response.body) throw await toError(response);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const parsed = parseSse(buffer + value);
      buffer = parsed.rest;
      for (const { event, data } of parsed.events) {
        const payload = data as Record<string, unknown>;
        if (event === "stage") onStage(String(payload.stage), payload);
        else if (event === "answer") return payload as unknown as AskResult;
        else if (event === "error") throw new ApiError(503, String(payload.code), String(payload.message));
      }
    }
    throw new ApiError(502, "incomplete", "The answer stream ended early.");
  },
};
