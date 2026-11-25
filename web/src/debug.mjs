export function redact(value, depth = 0) {
  if (depth > 16) return "[truncated: depth]";
  if (Array.isArray(value)) {
    const items = value.slice(0, 200).map((v) => redact(v, depth + 1));
    if (value.length > 200)
      items.push({ _truncated_items: value.length - 200 });
    return items;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value);
    const result = Object.fromEntries(
      entries
        .slice(0, 200)
        .map(([k, v]) => [
          k,
          /api[_-]?key|authorization|password|secret|access[_-]?token|admin[_-]?token/i.test(
            k,
          )
            ? "[redacted]"
            : redact(v, depth + 1),
        ]),
    );
    if (entries.length > 200) result._truncated_keys = entries.length - 200;
    return result;
  }
  if (typeof value === "string") {
    const clean = value.replace(
      /bearer\s+[^\s"']+|\bsk-[A-Za-z0-9_-]{8,}/gi,
      "[redacted]",
    );
    return clean.slice(0, 16000) + (clean.length > 16000 ? " [truncated]" : "");
  }
  return value;
}
export function timeline(runs) {
  if (!runs.length) return [];
  const start = Math.min(...runs.map((r) => Date.parse(r.started_at)));
  const end = Math.max(
    ...runs.map((r) => Date.parse(r.started_at) + Math.max(1, r.duration_ms)),
  );
  return runs.map((r) => ({
    id: r.id,
    left: ((Date.parse(r.started_at) - start) / (end - start)) * 100,
    width: Math.max(0.5, (r.duration_ms / (end - start)) * 100),
  }));
}
// Domain exports retain every item; the on-screen/network preview remains bounded.
export function exportJSON(value) {
  return JSON.stringify(
    value,
    (key, current) => {
      if (
        /api[_-]?key|authorization|password|secret|access[_-]?token|admin[_-]?token/i.test(
          key,
        )
      )
        return "[redacted]";
      return typeof current === "string"
        ? current.replace(
            /bearer\s+[^\s"']+|\bsk-[A-Za-z0-9_-]{8,}/gi,
            "[redacted]",
          )
        : current;
    },
    2,
  );
}
export function downloadJSON(name, value) {
  const url = URL.createObjectURL(
    new Blob([exportJSON(value)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
