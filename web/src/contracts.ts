export type Obj = Record<string, unknown>;
export function obj(v: unknown): Obj {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {};
}
export function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
export function text(v: unknown, fallback = "—"): string {
  return v === undefined || v === null || v === "null"
    ? fallback
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);
}
export type Evidence = {
  node_id: string;
  page?: number;
  char_span?: [number, number];
  cell?: [number, number];
};
export type Navigate = (evidence: Evidence) => void;
export function evidence(v: unknown): Evidence[] {
  const seen = new Set<string>();
  return arr(v).flatMap((item) => {
    const r = obj(item),
      ref = r.node_id ?? r.node_ref;
    if (typeof ref !== "string" || !ref.includes("/r")) return [];
    const key = JSON.stringify([ref, r.char_span, r.cell]);
    if (seen.has(key)) return [];
    seen.add(key);
    return [
      {
        node_id: ref,
        ...(typeof r.page === "number" ? { page: r.page } : {}),
        ...(Array.isArray(r.char_span) && r.char_span.length === 2
          ? { char_span: r.char_span as [number, number] }
          : {}),
        ...(Array.isArray(r.cell) && r.cell.length === 2
          ? { cell: r.cell as [number, number] }
          : {}),
      },
    ];
  });
}
export function nodePath(ref: string) {
  return "/api/v1/nodes/" + ref.split("/").map(encodeURIComponent).join("/");
}
export function refPage(ref: string): number | undefined {
  const m = ref.match(/\/p(\d+)\./);
  return m ? Number(m[1]) : undefined;
}
export type Compound = {
  compound_id: string;
  primary_label: string;
  aliases?: string[];
  role?: string;
  role_status?: string;
  status?: string;
  accepted_structure_id?: string | null;
  locked_fields?: string[];
  evidence?: unknown[];
  role_evidence?: unknown[];
};
export type Measurement = {
  measurement_id: string;
  compound_id?: string;
  subject_label?: string;
  metric: string;
  metric_label_raw?: string;
  value_raw: string;
  unit_raw?: string;
  value?: number | null;
  unit?: string;
  status: string;
  source_kind: string;
  assay_id?: string;
  evidence: unknown[];
  normalization_warning?: string;
  locked?: boolean;
};
export type FigureReport = {
  mode?: "full" | "query" | "review";
  report_id: string;
  node_ref: string;
  image_type: string;
  status: string;
  extraction?: Obj;
  backfilled_extraction?: Obj;
  checks?: Obj[];
  review?: Obj;
  open_questions?: string[];
  unchecked?: Obj[];
  accounting?: Obj;
  trace?: Obj[];
  run_id?: string;
  child_reports?: unknown[];
  image_products?: Obj[];
};
// Backend spans count Unicode code points; JavaScript string indices count UTF-16 units.
export function evidenceTextParts(
  source: string,
  span: unknown,
): [string, string, string] | null {
  if (
    !Array.isArray(span) ||
    span.length !== 2 ||
    !span.every(Number.isInteger)
  )
    return null;
  const chars = Array.from(source),
    [start, end] = span as number[];
  if (start < 0 || end > chars.length || start >= end) return null;
  return [
    chars.slice(0, start).join(""),
    chars.slice(start, end).join(""),
    chars.slice(end).join(""),
  ];
}

export type Box = { x0: number; y0: number; x1: number; y1: number };
export function normalizedBox(value: unknown): Box | null {
  const b = obj(value);
  if (
    !["x0", "y0", "x1", "y1"].every(
      (k) =>
        typeof b[k] === "number" &&
        Number.isFinite(b[k]) &&
        Number(b[k]) >= 0 &&
        Number(b[k]) <= 1,
    )
  )
    return null;
  const box = b as Box;
  return box.x1 > box.x0 && box.y1 > box.y0 ? box : null;
}
