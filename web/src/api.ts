import { redact } from "./debug.mjs";
export type JSONValue = unknown;
export type Log = {
  id: number;
  method: string;
  path: string;
  status: number;
  outcome: "success" | "http_error" | "aborted" | "timeout" | "network_error";
  ms: number;
  at: string;
  response: unknown;
  request?: unknown;
};
let logs: Log[] = [];
let sequence = 0;
const listeners = new Set<() => void>();
export const logStore = {
  subscribe(fn: () => void) {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
  getSnapshot: () => logs,
  clear() {
    logs = [];
    listeners.forEach((f) => f());
  },
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public payload: unknown,
    message?: string,
  ) {
    super(message ?? `HTTP ${status}: ${JSON.stringify(redact(payload))}`);
    this.name = "ApiError";
  }
}
export async function request<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    signal?: AbortSignal;
    timeout?: number;
  } = {},
): Promise<T> {
  const { method = "GET", body, signal, timeout = 30000 } = options;
  const started = performance.now(),
    controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    abort();
  }, timeout);
  let status = 0,
    response: unknown;
  let outcome: Log["outcome"] = "success";
  try {
    const result = await fetch(path, {
      method,
      signal: controller.signal,
      headers:
        body instanceof FormData
          ? undefined
          : { "Content-Type": "application/json" },
      body:
        body instanceof FormData
          ? body
          : body === undefined
            ? undefined
            : JSON.stringify(body),
    });
    status = result.status;
    const content = await result.text();
    try {
      response = JSON.parse(content);
    } catch {
      response = content;
    }
    if (!result.ok) throw new ApiError(status, response);
    if (!result.headers.get("content-type")?.includes("application/json"))
      throw new ApiError(
        status,
        response,
        "接口未返回 JSON，请检查前端代理目标和后端路由。",
      );
    return response as T;
  } catch (error) {
    outcome = timedOut
      ? "timeout"
      : controller.signal.aborted
        ? "aborted"
        : error instanceof ApiError
          ? "http_error"
          : "network_error";
    if (!status)
      response = error instanceof Error ? error.message : String(error);
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    logs = [
      {
        id: ++sequence,
        method,
        path,
        status,
        outcome,
        ms: Math.round(performance.now() - started),
        at: new Date().toISOString(),
        response: redact(response),
        request: body instanceof FormData ? "[PDF upload]" : redact(body),
      },
      ...logs,
    ].slice(0, 100);
    listeners.forEach((f) => f());
  }
}
export type Doc = {
  document_id: string;
  title: string | null;
  filename: string | null;
  status: string;
  active_revision: number | null;
  quality: Record<string, unknown>;
  error: unknown;
};
export type Run = {
  id: string;
  document_id: string;
  run_type: string;
  component: string;
  component_version: string | null;
  parent_run_id: string | null;
  trace_id: string | null;
  status: string;
  metrics: Record<string, unknown>;
  error: unknown;
  started_at: string;
  ended_at: string | null;
  duration_ms: number;
};
export type Runs = {
  runs: Run[];
  total: number;
  limit: number;
  offset: number;
  observed_at: string;
  scope: string;
};
export type Tree = {
  id: string;
  type: string;
  label?: string;
  text?: string;
  page: number | null;
  children: Tree[];
};
export type Node = {
  node_id: string;
  type: string;
  label: string | null;
  page: number | null;
  preview?: string;
  regions: {
    page: number;
    bbox: { x0: number; y0: number; x1: number; y1: number };
    derived?: boolean;
  }[];
};
