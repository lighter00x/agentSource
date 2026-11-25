import { useEffect, useState } from "react";
import { request } from "./api";
import { redact } from "./debug.mjs";
import { JsonViewer } from "./json-viewer";
export const pretty = (v: unknown) => JSON.stringify(redact(v), null, 2);
export function Raw({ value }: { value: unknown }) {
  return <JsonViewer value={value} />;
}
export function Badge({ status }: { status: string }) {
  return (
    <span
      className={`badge ${["failed", "error", "incorrect"].includes(status) ? "bad" : ["running", "pending", "needs_review", "parsing", "warning", "unclear", "unsure", "unknown", "unchecked", "not_determined"].includes(status) ? "warn" : "neutral"}`}
    >
      {status}
    </span>
  );
}
export function useResource<T>(path: string | null, interval = 0) {
  const [state, setState] = useState<{
    key: string | null;
    data?: T;
    error?: Error;
    loading: boolean;
    at?: string;
  }>({ key: null, loading: false });
  const [version, update] = useState(0);
  useEffect(() => {
    setState((prev) =>
      prev.key === path
        ? { ...prev, loading: !!path, error: undefined }
        : { key: path, loading: !!path },
    );
    if (!path) return;
    let active = true,
      timer: ReturnType<typeof setTimeout>,
      controller: AbortController;
    const load = async () => {
      controller = new AbortController();
      try {
        const data = await request<T>(path, { signal: controller.signal });
        if (active)
          setState({
            key: path,
            data,
            loading: false,
            at: new Date().toLocaleTimeString("zh-CN"),
          });
      } catch (error) {
        if (active)
          setState((prev) => ({
            ...prev,
            key: path,
            loading: false,
            error: error instanceof Error ? error : new Error(String(error)),
          }));
      }
      if (active && interval) timer = setTimeout(load, interval);
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [path, interval, version]);
  const current = state.key === path ? state : { key: path, loading: !!path };
  return { ...current, refresh: () => update((v) => v + 1) };
}
export function ResourceState({
  loading,
  error,
}: {
  loading: boolean;
  error?: Error;
}) {
  return (
    <>
      {loading && <p className="muted">正在读取…</p>}
      {error && (
        <div role="alert" className="error">
          {error.message}
        </div>
      )}
    </>
  );
}
