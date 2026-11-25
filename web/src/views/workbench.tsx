import { useEffect, useState, type PointerEvent } from "react";
import { request, type Doc } from "../api";
import { downloadJSON } from "../debug.mjs";
import { Raw, ResourceState, useResource, Badge } from "../ui";
import { Molecule } from "./molecule";
import { OcrContent } from "./ocr";
import { FigureResultFile } from "./figure-result";
import { AgentTimeline } from "./agent-timeline";
import { StageResult, statusLabel, type StageStatus } from "./stage-result";
import { StageTimeline } from "./stage-timeline";
import { RecognitionTimeline } from "./recognition-timeline";
import { StructureInputs } from "./structure-inputs";

type Box = { x0: number; y0: number; x1: number; y1: number };
type AgentGap = {
  gap_id: string;
  question: string;
  status: string;
  field_paths: string[];
  evidence_ids: string[];
  note?: string;
  changes?: unknown;
};

function AgentEvidenceOverview({ records }: { records: unknown[] }) {
  const snapshots = records as {
    kind?: string;
    gaps?: AgentGap[];
    active_images?: number;
    request_bytes?: number;
    remaining?: { tools: number; visual_reads: number; extractions: number };
    applied_evidence_ids?: string[];
    unapplied_evidence?: string[];
    context_usage?: { active_images?: number; request_bytes?: number };
  }[];
  const latest = snapshots.filter((s) => s.gaps).at(-1);
  const context = snapshots.filter((s) => s.kind === "context").at(-1);
  if (!latest && !context) return null;
  const labels: Record<string, string> = {
    open: "待补充",
    resolved: "已应用并校验",
    conflict: "证据冲突",
    unresolved: "仍待复核",
  };
  return (
    <details>
      <summary>问题与证据 · {latest?.gaps?.length || 0} 个信息缺口</summary>
      {context && (
        <p>
          当前请求 {context.active_images} 张图像 · 约{" "}
          {Math.ceil((context.request_bytes || 0) / 1024)} KB。 剩余工具{" "}
          {context.remaining?.tools} 次、图像查看{" "}
          {context.remaining?.visual_reads} 次、成功提取{" "}
          {context.remaining?.extractions} 次。
        </p>
      )}
      {latest?.gaps?.map((gap) => (
        <details key={gap.gap_id}>
          <summary>
            {labels[gap.status] || gap.status} · {gap.question}
          </summary>
          <p>影响字段：{gap.field_paths.join("、")}</p>
          <p>证据：{gap.evidence_ids.join("、") || "尚未关联"}</p>
          {gap.note && <p>{gap.note}</p>}
          <Raw value={gap.changes || {}} />
        </details>
      ))}
      {latest?.applied_evidence_ids && (
        <p>
          已用于当前结果：{latest.applied_evidence_ids.join("、") || "无"}。
        </p>
      )}
      {!!latest?.unapplied_evidence?.length && (
        <p>尚未应用：{latest.unapplied_evidence.join("、")}。</p>
      )}
    </details>
  );
}
type Region = { page: number; bbox: Box };
type Stage = { id: string; label: string };
type Run = {
  id: string;
  status: string;
  start: string;
  next_stage?: string;
  source_run?: string;
  created_at: string;
  steps: {
    stage: string;
    status: string;
    source_run?: string;
    error?: string;
    duration_ms?: number;
  }[];
  error?: string;
  correction_version: number;
  draft_version: number;
};
type Figure = {
  node_ref: string;
  label?: string;
  type: string;
  regions: Region[];
  latest?: Run;
};
type Overview = {
  revision: number;
  stages: Stage[];
  figures: Figure[];
  counts: Record<string, number>;
  image_types: string[];
  mode: string;
};
type Mol = {
  node_ref: string;
  status: string;
  geometry: { regions: Region[]; local_bbox: Box; parent_region_index: number };
  attributes: Record<string, unknown>;
  structure?: {
    formula?: string;
    canonical_smiles?: string;
    smiles_core?: string;
    e_smiles_raw?: string;
    validation?: unknown;
  };
  manual_smiles: boolean;
  manual_box: boolean;
};
type Molecules = {
  version: number;
  image_type?: string;
  molecules: Mol[];
  history: { version: number; stale_from: string }[];
};
type Detail = {
  process_error?: string;
  run: Run;
  states: Record<string, Record<string, unknown>>;
  events: { kind: string; stage: string; at: string; data: unknown }[];
};
const enc = encodeURIComponent;
const titles: Record<string, string> = {
  workflow: "全流程工作区",
  layout: "MinerU 布局",
  "figure-debug": "图表逐步解析",
  "molecule-debug": "分子检测与修正",
  "skill-editor": "局部 Skill 编辑",
};
function rect(b: Box) {
  return {
    left: `${b.x0 * 100}%`,
    top: `${b.y0 * 100}%`,
    width: `${(b.x1 - b.x0) * 100}%`,
    height: `${(b.y1 - b.y0) * 100}%`,
  };
}
function imageRefs(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(imageRefs);
  if (value && typeof value === "object")
    return Object.entries(value).flatMap(([k, v]) =>
      k === "blob_ref" && typeof v === "string"
        ? [v]
        : k === "image_refs" && Array.isArray(v)
          ? v.filter((x): x is string => typeof x === "string")
          : imageRefs(v),
    );
  return [];
}
// Reused stages copy snapshots but keep their skill payloads in the source run.
function SavedStageResult({
  base,
  stage,
  events,
  status,
  detectionEvents = [],
  recognitionStructures = [],
  ...props
}: {
  base: string;
  stage: string;
  label: string;
  state?: Record<string, unknown>;
  events: Detail["events"];
  status?: StageStatus;
  detectionEvents?: Detail["events"];
  recognitionStructures?: unknown[];
}) {
  const [source, setSource] = useState<{
    key: string;
    events: Detail["events"];
    detectionEvents?: Detail["events"];
    structures?: unknown[];
    error?: string;
  }>();
  const origin = status?.source_run;
  const key = `${base}:${stage}:${origin}`;
  const hasResults = events.some((e) => e.kind === "skill_result");
  useEffect(() => {
    if (!origin || hasResults) return;
    const controller = new AbortController();
    void (async () => {
      const seen = new Set<string>();
      let id: string | undefined = origin;
      try {
        while (id && !seen.has(id)) {
          seen.add(id);
          const data: Detail = await request(
            `${base}/workbench/runs/${enc(id)}`,
            { signal: controller.signal },
          );
          const saved = data.events?.filter((e) => e.stage === stage) || [];
          if (saved.some((e) => e.kind === "skill_result")) {
            if (!controller.signal.aborted)
              setSource({
                key,
                events: saved,
                detectionEvents: data.events.filter(
                  (e) => e.stage === "detect",
                ),
                structures: (
                  data.states.structure?.material as { structures?: unknown[] }
                )?.structures,
              });
            return;
          }
          id = data.run?.steps.find((s) => s.stage === stage)?.source_run;
        }
        if (!controller.signal.aborted) setSource({ key, events: [] });
      } catch (e) {
        if (!controller.signal.aborted)
          setSource({ key, events: [], error: String(e) });
      }
    })();
    return () => controller.abort();
  }, [base, stage, origin, hasResults, key]);
  const saved = source?.key === key ? source : undefined;
  return (
    <>
      {origin && !hasResults && !saved && (
        <p className="muted">正在读取上游步骤的调用结果…</p>
      )}
      {saved?.error && (
        <p className="muted">
          上游调用记录暂不可用，显示当前运行保存的状态。{saved.error}
        </p>
      )}
      <StageResult
        {...props}
        stage={stage}
        status={status}
        events={
          hasResults ? events : saved?.events.length ? saved.events : events
        }
      >
        {stage === "recognize" ? (
          <RecognitionTimeline
            events={
              hasResults ? events : saved?.events.length ? saved.events : events
            }
            detectionEvents={
              saved?.detectionEvents?.length
                ? saved.detectionEvents
                : detectionEvents
            }
            structures={saved?.structures || recognitionStructures}
            running={status?.status === "running"}
          />
        ) : stage === "structure" ? (
          <StructureInputs
            events={
              hasResults ? events : saved?.events.length ? saved.events : events
            }
          />
        ) : (
          stage !== "structure" && (
            <StageTimeline
              events={
                hasResults
                  ? events
                  : saved?.events.length
                    ? saved.events
                    : events
              }
              running={status?.status === "running"}
              inherited={!!origin}
            />
          )
        )}
      </StageResult>
    </>
  );
}
function StageRecords({
  events,
  stage,
}: {
  events: Detail["events"];
  stage: string;
}) {
  const [open, setOpen] = useState(false);
  if (!events.length) return null;
  return (
    <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>原始模型与阶段记录 · {events.length} 条</summary>
      {open &&
        events.map((event, i) => (
          <details key={i}>
            <summary>
              {event.kind} · {event.at}
            </summary>
            {[...new Set(imageRefs(event.data))].map((ref) => (
              <img
                className="debug-image"
                key={ref}
                alt="过程图像"
                src={`/api/v1/blobs/${ref}`}
              />
            ))}
            <button
              onClick={() => downloadJSON(`step-${stage}-${i}.json`, event)}
            >
              导出完整步骤记录
            </button>
            <Raw value={event.data} />
          </details>
        ))}
    </details>
  );
}
export function WorkbenchView({
  base,
  doc,
  section,
  interval,
}: {
  base: string;
  doc: Doc;
  section: string;
  interval: number;
}) {
  const [page, setPage] = useState(0),
    [selected, setSelected] = useState(""),
    [runId, setRunId] = useState(""),
    [step, setStep] = useState("classify"),
    [stop, setStop] = useState(""),
    [before, setBefore] = useState(""),
    [start, setStart] = useState("classify"),
    [skill, setSkill] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [molRef, setMolRef] = useState(""),
    [smiles, setSmiles] = useState(""),
    [imageType, setImageType] = useState(""),
    [draw, setDraw] = useState(false),
    [origin, setOrigin] = useState<{ x: number; y: number } | null>(null),
    [draftBox, setDraftBox] = useState<Box | null>(null),
    [boxText, setBoxText] = useState("0.1,0.1,0.4,0.4"),
    [segment, setSegment] = useState(0);
  const [zoom, setZoom] = useState(100),
    [imageError, setImageError] = useState(false);
  useEffect(() => setImageError(false), [page]);
  const [compareId, setCompareId] = useState("");
  const compare = useResource<Detail>(
    compareId ? `${base}/workbench/runs/${enc(compareId)}` : null,
  );
  const overview = useResource<Overview>(`${base}/workbench`, interval);
  const layout = useResource<{
    nodes: {
      node_id: string;
      type: string;
      label?: string;
      regions: Region[];
      text_raw?: string;
    }[];
  }>(`${base}/workbench/layout?page=${page}`, interval);
  const figure = overview.data?.figures.find((f) => f.node_ref === selected);
  const history = useResource<Run[]>(
    figure ? `${base}/workbench/runs?node_ref=${enc(selected)}` : null,
    interval,
  );
  const detail = useResource<Detail>(
    runId ? `${base}/workbench/runs/${enc(runId)}` : null,
    interval,
  );
  const molecules = useResource<Molecules>(
    figure ? `${base}/workbench/molecules?node_ref=${enc(selected)}` : null,
    interval,
  );
  const node = useResource<Record<string, unknown>>(
    selected ? `/api/v1/nodes/${selected}` : null,
  );
  const mol = molecules.data?.molecules.find((m) => m.node_ref === molRef);
  const current = detail.data?.run;
  const running = history.data?.some((r) =>
    ["queued", "running"].includes(r.status),
  );
  useEffect(() => {
    if (!selected && overview.data?.figures[0])
      setSelected(overview.data.figures[0].node_ref);
  }, [overview.data, selected]);
  useEffect(() => {
    setRunId("");
    setCompareId("");
    setMolRef("");
    setDraw(false);
    setError("");
  }, [selected]);
  useEffect(() => {
    if (!runId && history.data?.[0]) setRunId(history.data[0].id);
  }, [history.data, runId]);
  useEffect(() => {
    setSmiles(
      mol?.structure?.e_smiles_raw || mol?.structure?.canonical_smiles || "",
    );
    if (mol) {
      const b = mol.geometry.local_bbox;
      setBoxText([b.x0, b.y0, b.x1, b.y1].join(","));
      setSegment(mol.geometry.parent_region_index);
    }
  }, [mol]);
  useEffect(
    () => setImageType(molecules.data?.image_type || ""),
    [molecules.data?.image_type, selected],
  );
  const refresh = () => {
    overview.refresh();
    layout.refresh();
    history.refresh();
    detail.refresh();
    molecules.refresh();
    node.refresh();
  };
  async function action(path: string, body: unknown, method = "POST") {
    setError("");
    setBusy(true);
    try {
      const r = await request<{ id?: string }>(`${base}/workbench/${path}`, {
        method,
        body,
        timeout: 120000,
      });
      if (r.id) setRunId(r.id);
      refresh();
      return r;
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const correction = (body: Record<string, unknown>) =>
    action("corrections", {
      node_ref: selected,
      expected_version: molecules.data?.version ?? 0,
      ...body,
    });
  async function execute(from: string, continuing = false) {
    await action("runs", {
      node_ref: selected,
      start: from,
      source_run: from === "classify" ? undefined : runId,
      stop_after: continuing ? undefined : stop || undefined,
      pause_before: continuing ? undefined : before || undefined,
      skill: skill || undefined,
    });
  }
  function choose(ref: string, p?: number) {
    setSelected(ref);
    if (p !== undefined) setPage(p);
  }
  function position(e: PointerEvent<HTMLDivElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    };
  }
  async function finishDraw(e: PointerEvent<HTMLDivElement>) {
    if (!draw || !origin || !figure) return;
    const end = position(e),
      b = {
        x0: Math.min(origin.x, end.x),
        y0: Math.min(origin.y, end.y),
        x1: Math.max(origin.x, end.x),
        y1: Math.max(origin.y, end.y),
      };
    setOrigin(null);
    setDraftBox(null);
    const i = figure.regions.findIndex(
      (r) =>
        r.page === page &&
        b.x0 >= r.bbox.x0 &&
        b.y0 >= r.bbox.y0 &&
        b.x1 <= r.bbox.x1 &&
        b.y1 <= r.bbox.y1,
    );
    if (i < 0 || b.x1 - b.x0 < 0.002 || b.y1 - b.y0 < 0.002) {
      setError("请在选中图表的区域内绘制一个有效分子框");
      return;
    }
    const p = figure.regions[i].bbox;
    await correction({
      kind: "box",
      molecule_ref: molRef || undefined,
      segment: i,
      bbox: {
        x0: (b.x0 - p.x0) / (p.x1 - p.x0),
        y0: (b.y0 - p.y0) / (p.y1 - p.y0),
        x1: (b.x1 - p.x0) / (p.x1 - p.x0),
        y1: (b.y1 - p.y0) / (p.y1 - p.y0),
      },
    });
    setDraw(false);
  }
  const stages = overview.data?.stages || [];
  const latestState = detail.data?.states[step];
  const routeState = detail.data?.states.plan || detail.data?.states.classify;
  return (
    <>
      <div className="section-head">
        <div>
          <h1>{titles[section]}</h1>
          <p className="muted">
            选择图表 → 自动判断与路由 → 分步检查 → 局部修改与重跑
          </p>
        </div>
        <button onClick={refresh}>刷新过程</button>
      </div>
      <ResourceState {...overview} />
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="card toolbar">
        <span>
          文献模式：{overview.data?.mode === "auto" ? "自动" : "手动"}
        </span>
        {Object.entries(overview.data?.counts || {}).map(([k, v]) => (
          <span key={k}>
            {k}: {v}
          </span>
        ))}
        <button disabled={busy} onClick={() => void action("auto", {})}>
          处理未完成 / 问题图表
        </button>
        <span className="muted">
          单图失败不阻塞其他图表 · 手动模式默认按需处理
        </span>
      </div>
      <div className="workbench-grid">
        <section className="card pdf-debug">
          <div className="toolbar">
            <button disabled={!page} onClick={() => setPage((p) => p - 1)}>
              上一页
            </button>
            <label>
              页码
              <input
                aria-label="调试页码"
                type="number"
                min="1"
                value={page + 1}
                onChange={(e) =>
                  setPage(Math.max(0, Number(e.target.value) - 1))
                }
              />
            </label>
            <button
              disabled={
                page + 1 >=
                Number(
                  doc.quality.page_count_pdf || doc.quality.page_count_layout,
                )
              }
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </button>
            <span>合并布局 · 点击选中</span>
            <label>
              缩放
              <select
                aria-label="PDF 缩放"
                value={zoom}
                onChange={(e) => setZoom(Number(e.target.value))}
              >
                {[100, 125, 150, 200].map((z) => (
                  <option key={z} value={z}>
                    {z}%
                  </option>
                ))}
              </select>
            </label>
          </div>
          <ResourceState {...layout} />
          {imageError && (
            <p className="error">PDF 页面图加载失败，请检查页码与源文件。</p>
          )}
          <div
            className="paper"
            style={{
              width: `${zoom}%`,
              touchAction: draw ? "none" : "auto",
              cursor: draw ? "crosshair" : undefined,
            }}
            onPointerDown={(e) => {
              if (draw) {
                e.currentTarget.setPointerCapture(e.pointerId);
                setOrigin(position(e));
              }
            }}
            onPointerMove={(e) => {
              if (draw && origin) {
                const p = position(e);
                setDraftBox({
                  x0: Math.min(origin.x, p.x),
                  y0: Math.min(origin.y, p.y),
                  x1: Math.max(origin.x, p.x),
                  y1: Math.max(origin.y, p.y),
                });
              }
            }}
            onPointerUp={(e) => void finishDraw(e)}
          >
            <img
              onError={() => setImageError(true)}
              draggable={false}
              alt={`PDF 第 ${page + 1} 页`}
              src={`${base}/pages/${page}/image?dpi=144`}
            />
            {layout.data?.nodes.flatMap((n) =>
              n.regions.map((r, i) => (
                <button
                  key={`${n.node_id}-${i}`}
                  aria-label={n.label || n.node_id}
                  title={`${n.type} ${n.label || ""}`}
                  className={`region ${n.node_id === selected ? "active" : ""}`}
                  style={{
                    ...rect(r.bbox),
                    pointerEvents: draw ? "none" : undefined,
                  }}
                  onClick={() => {
                    if (!draw) choose(n.node_id);
                  }}
                />
              )),
            )}
            {molecules.data?.molecules
              .filter((m) => m.status !== "removed")
              .flatMap((m) =>
                m.geometry.regions
                  .filter((r) => r.page === page)
                  .map((r, i) => (
                    <button
                      key={m.node_ref + i}
                      aria-label={`分子 ${m.node_ref}`}
                      title="点击查看分子式与SMILES"
                      className={`region mol-region ${molRef === m.node_ref ? "active" : ""}`}
                      style={{
                        ...rect(r.bbox),
                        pointerEvents: draw ? "none" : undefined,
                      }}
                      onClick={() => setMolRef(m.node_ref)}
                    />
                  )),
              )}
            {draftBox && (
              <div
                className="region active"
                style={{ ...rect(draftBox), pointerEvents: "none" }}
              />
            )}
          </div>
        </section>
        <aside className="debug-inspector">
          <section className="card">
            <h2>图表与来源</h2>
            <select
              aria-label="调试图表"
              value={figure ? selected : ""}
              onChange={(e) => {
                const f = overview.data?.figures.find(
                  (x) => x.node_ref === e.target.value,
                );
                if (f) choose(f.node_ref, f.regions[0]?.page);
              }}
            >
              <option value="">选择图表</option>
              {overview.data?.figures.map((f) => (
                <option key={f.node_ref} value={f.node_ref}>
                  {f.label || f.node_ref.split("/").at(-1)} ·{" "}
                  {f.latest?.status || "未处理"}
                </option>
              ))}
            </select>
            {selected && (
              <>
                <code>{selected}</code>
                <details>
                  <summary>MinerU 文本 / 原始节点与成员</summary>
                  <Raw value={node.data} />
                </details>
              </>
            )}
            {!figure && selected && <OcrContent node={node.data} />}
          </section>
          {figure && (
            <>
              <section className="card">
                <h2>单图执行</h2>
                <div className="toolbar">
                  <button
                    disabled={busy || running}
                    onClick={() => void execute("classify")}
                  >
                    解析此图（自动分类与路由）
                  </button>
                  <button
                    disabled={busy || running}
                    onClick={() =>
                      void action("runs", {
                        node_ref: selected,
                        stop_after: "classify",
                      })
                    }
                  >
                    仅判断类型
                  </button>
                  {running && (
                    <button
                      onClick={() =>
                        void action(
                          `runs/${history.data?.find((r) => ["queued", "running"].includes(r.status))?.id}/pause`,
                          {},
                        )
                      }
                    >
                      当前步骤后暂停
                    </button>
                  )}
                </div>
                <label>
                  人工类型覆盖
                  <select
                    value={imageType}
                    onChange={(e) => setImageType(e.target.value)}
                  >
                    <option value="">采用自动分类</option>
                    {overview.data?.image_types.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={busy || running}
                  onClick={() =>
                    void correction({
                      kind: "type",
                      image_type: imageType || null,
                    })
                  }
                >
                  保存类型并在下次运行重新路由
                </button>
                <details>
                  <summary>执行范围与调试 Skill 指定</summary>
                  <label>
                    执行后暂停
                    <select
                      value={stop}
                      onChange={(e) => setStop(e.target.value)}
                    >
                      <option value="">完整执行</option>
                      {stages.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    执行前暂停
                    <select
                      value={before}
                      onChange={(e) => setBefore(e.target.value)}
                    >
                      <option value="">不预设</option>
                      {stages.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    指定结构化 Skill（可选）
                    <input
                      value={skill}
                      onChange={(e) => setSkill(e.target.value)}
                      placeholder="留空自动选择，如 table_extract"
                    />
                  </label>
                </details>
                <label>
                  历史运行
                  <select
                    aria-label="调试运行"
                    value={runId}
                    onChange={(e) => setRunId(e.target.value)}
                  >
                    <option value="">选择运行</option>
                    {history.data?.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.created_at} · {r.status} · {r.id}
                      </option>
                    ))}
                  </select>
                </label>
                <ResourceState {...history} />
                {current && (
                  <>
                    <Badge status={current.status} />
                    {current.error && <p className="error">{current.error}</p>}
                    {molecules.data &&
                      current.correction_version < molecules.data.version && (
                        <p className="warning-banner">
                          人工结果已更新，相关下游需要重跑。
                        </p>
                      )}
                    <label>
                      重跑起点
                      <select
                        value={start}
                        onChange={(e) => setStart(e.target.value)}
                      >
                        {stages.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      disabled={busy || running}
                      onClick={() => void execute(start)}
                    >
                      从此步骤创建新运行
                    </button>
                    {current.status === "paused" && current.next_stage && (
                      <button
                        disabled={busy || running}
                        onClick={() => void execute(current.next_stage!, true)}
                      >
                        继承上游并继续
                      </button>
                    )}
                  </>
                )}
              </section>
              {current?.steps.some(
                (s) =>
                  ["post", "report"].includes(s.stage) &&
                  ["completed", "reused"].includes(s.status),
              ) && (
                <section className="card">
                  <FigureResultFile
                    endpoint={`${base}/workbench/runs/${enc(current.id)}/result`}
                    interval={interval}
                  />
                </section>
              )}
              {(section === "workflow" ||
                section === "figure-debug" ||
                section === "layout") && (
                <section className="card">
                  <h2>处理步骤与实际调用</h2>
                  <ResourceState {...detail} />
                  {detail.data?.process_error && (
                    <details open>
                      <summary>执行进程启动错误</summary>
                      <pre>{detail.data.process_error}</pre>
                    </details>
                  )}
                  <div className="stage-list">
                    {stages.map((s) => {
                      const status = current?.steps.find(
                        (x) => x.stage === s.id,
                      );
                      return (
                        <button
                          key={s.id}
                          className={step === s.id ? "selected" : ""}
                          onClick={() => setStep(s.id)}
                        >
                          {s.label} ·{" "}
                          {status ? statusLabel(status.status) : "未执行"}
                          {status?.source_run && " · 继承上游"}
                          {status?.duration_ms !== undefined &&
                            ` · ${status.duration_ms}ms`}
                        </button>
                      );
                    })}
                  </div>
                  <SavedStageResult
                    base={base}
                    key={`${runId}-${step}-result`}
                    stage={step}
                    label={stages.find((s) => s.id === step)?.label || step}
                    state={latestState}
                    detectionEvents={
                      detail.data?.events.filter((e) => e.stage === "detect") ||
                      []
                    }
                    recognitionStructures={
                      (
                        detail.data?.states.structure?.material as {
                          structures?: unknown[];
                        }
                      )?.structures || []
                    }
                    events={
                      detail.data?.events.filter((e) => e.stage === step) || []
                    }
                    status={current?.steps.find((s) => s.stage === step)}
                  />
                  <label>
                    对比历史运行
                    <select
                      aria-label="对比运行"
                      value={compareId}
                      onChange={(e) => setCompareId(e.target.value)}
                    >
                      <option value="">选择另一次运行</option>
                      {history.data
                        ?.filter((r) => r.id !== runId)
                        .map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.created_at} · {r.id}
                          </option>
                        ))}
                    </select>
                  </label>
                  {compareId && (
                    <details open>
                      <summary>同一步骤结果对比</summary>
                      <div className="code-diff">
                        <div>
                          当前运行
                          <SavedStageResult
                            base={base}
                            stage={step}
                            label="当前运行"
                            state={latestState}
                            detectionEvents={
                              detail.data?.events.filter(
                                (e) => e.stage === "detect",
                              ) || []
                            }
                            recognitionStructures={
                              (
                                detail.data?.states.structure?.material as {
                                  structures?: unknown[];
                                }
                              )?.structures || []
                            }
                            events={
                              detail.data?.events.filter(
                                (e) => e.stage === step,
                              ) || []
                            }
                            status={current?.steps.find(
                              (s) => s.stage === step,
                            )}
                          />
                        </div>
                        <div>
                          对比运行
                          <SavedStageResult
                            base={base}
                            stage={step}
                            label="对比运行"
                            state={compare.data?.states[step]}
                            detectionEvents={
                              compare.data?.events.filter(
                                (e) => e.stage === "detect",
                              ) || []
                            }
                            recognitionStructures={
                              (
                                compare.data?.states.structure?.material as {
                                  structures?: unknown[];
                                }
                              )?.structures || []
                            }
                            events={
                              compare.data?.events.filter(
                                (e) => e.stage === step,
                              ) || []
                            }
                            status={compare.data?.run.steps.find(
                              (s) => s.stage === step,
                            )}
                          />
                        </div>
                      </div>
                    </details>
                  )}
                  {step === "structure" && (
                    <section aria-label="图表 Agent 探索过程">
                      <h3>Agent 探索过程</h3>
                      <button
                        disabled={
                          busy ||
                          running ||
                          !runId ||
                          !detail.data?.states.structure
                        }
                        onClick={() =>
                          void action("runs", {
                            node_ref: selected,
                            start: "structure",
                            source_run: runId,
                            reuse_structure: true,
                          })
                        }
                      >
                        沿用结果继续审查与局部修正
                      </button>
                      <p>
                        查看决策、工具调用、证据和停止原因。全文读取仅限当前文献，解析操作仅限当前图。
                      </p>
                      <AgentEvidenceOverview
                        records={
                          detail.data?.events
                            .filter(
                              (e) =>
                                e.stage === step &&
                                e.kind === "agent_exploration",
                            )
                            .map((e) => e.data) || []
                        }
                      />
                      <AgentTimeline
                        key={runId}
                        events={detail.data?.events || []}
                        exploration={
                          detail.data?.states.structure?.exploration as
                            Record<string, unknown> | undefined
                        }
                        base={base}
                        running={running}
                      />
                    </section>
                  )}
                  <StageRecords
                    key={`${runId}-${step}`}
                    stage={step}
                    events={
                      detail.data?.events.filter(
                        (e) =>
                          e.stage === step && e.kind !== "agent_exploration",
                      ) || []
                    }
                  />
                </section>
              )}
              {(section === "workflow" || section === "molecule-debug") && (
                <section className="card">
                  <h2>分子详情与人工修正</h2>
                  <div className="toolbar">
                    <button
                      disabled={busy || running}
                      onClick={() =>
                        void action("runs", {
                          node_ref: selected,
                          operation: "detect",
                        })
                      }
                    >
                      仅检测分子
                    </button>
                    <button
                      disabled={busy || running}
                      onClick={() =>
                        void action("runs", {
                          node_ref: selected,
                          operation: "recognize",
                        })
                      }
                    >
                      识别当前全部分子
                    </button>
                    <button
                      disabled={busy || running || !mol}
                      onClick={() =>
                        void action("runs", {
                          node_ref: selected,
                          operation: "recognize_one",
                          molecule_ref: molRef,
                        })
                      }
                    >
                      仅重识别此分子
                    </button>
                    <button
                      disabled={busy || running || !runId}
                      onClick={() =>
                        void action("runs", {
                          node_ref: selected,
                          source_run: runId,
                          reuse_molecules: true,
                        })
                      }
                    >
                      复用当前预处理并解析
                    </button>
                  </div>
                  <p className="muted">
                    点击左侧分子框；绘制框使用当前图表分段的局部坐标。
                  </p>
                  <select
                    aria-label="选择分子"
                    value={molRef}
                    onChange={(e) => setMolRef(e.target.value)}
                  >
                    <option value="">新增分子 / 未选中</option>
                    {molecules.data?.molecules.map((m) => (
                      <option key={m.node_ref} value={m.node_ref}>
                        {m.node_ref.split("/").at(-1)} · {m.status}
                        {m.manual_smiles || m.manual_box ? " · 人工" : ""}
                      </option>
                    ))}
                  </select>
                  {mol && (
                    <>
                      <p>
                        分子式：{mol.structure?.formula || "未识别或结构不完整"}
                      </p>
                      <code>
                        {mol.structure?.e_smiles_raw ||
                          mol.structure?.canonical_smiles ||
                          "尚无 SMILES"}
                      </code>
                      <Molecule
                        smiles={
                          mol.structure?.canonical_smiles ||
                          mol.structure?.smiles_core ||
                          ""
                        }
                        fallback={`/api/v1/nodes/${mol.node_ref}/crop`}
                      />
                    </>
                  )}
                  <label>
                    SMILES
                    <textarea
                      aria-label="分子 SMILES"
                      value={smiles}
                      onChange={(e) => setSmiles(e.target.value)}
                    />
                  </label>
                  <button
                    disabled={!mol || busy || running}
                    onClick={() =>
                      void correction({
                        kind: "smiles",
                        molecule_ref: molRef,
                        smiles,
                      })
                    }
                  >
                    校验并保存局部 SMILES
                  </button>
                  <div className="toolbar">
                    <button
                      disabled={busy || running}
                      onClick={() => {
                        setDraw(!draw);
                        setError("");
                      }}
                    >
                      {draw
                        ? "取消绘制"
                        : mol
                          ? "在 PDF 绘制新位置"
                          : "在 PDF 绘制新增框"}
                    </button>
                    <button
                      disabled={!mol || busy || running}
                      onClick={() =>
                        void correction({
                          kind: "box",
                          molecule_ref: molRef,
                          bbox: mol?.geometry.local_bbox,
                          segment: mol?.geometry.parent_region_index,
                          deleted: true,
                        })
                      }
                    >
                      删除框
                    </button>
                    <button
                      disabled={!mol || busy || running}
                      onClick={() =>
                        void correction({
                          kind: "restore",
                          molecule_ref: molRef,
                        })
                      }
                    >
                      恢复机器原始结果
                    </button>
                  </div>
                  <details>
                    <summary>精确修改框 / 查看识别材料</summary>
                    <label>
                      局部框 x0,y0,x1,y1
                      <input
                        value={boxText}
                        onChange={(e) => setBoxText(e.target.value)}
                      />
                    </label>
                    <label>
                      跨页分段（从0开始）
                      <input
                        type="number"
                        min="0"
                        value={segment}
                        onChange={(e) => setSegment(Number(e.target.value))}
                      />
                    </label>
                    <button
                      disabled={busy || running}
                      onClick={() => {
                        const [x0, y0, x1, y1] = boxText.split(",").map(Number);
                        void correction({
                          kind: "box",
                          molecule_ref: molRef || undefined,
                          bbox: { x0, y0, x1, y1 },
                          segment,
                        });
                      }}
                    >
                      保存分子框
                    </button>
                    <Raw value={mol} />
                  </details>
                  <p className="muted">
                    重识别可选择“分子识别”作为重跑起点；正确识别后从“结构化提取”重跑可复用上游。
                  </p>
                </section>
              )}
              {(section === "workflow" || section === "skill-editor") && (
                <SkillEditor
                  base={base}
                  runId={runId}
                  nodeRef={selected}
                  busy={!!running}
                  chain={
                    Array.isArray(routeState?.chain)
                      ? (routeState.chain as string[])
                      : []
                  }
                  onChange={refresh}
                />
              )}
            </>
          )}
        </aside>
      </div>
    </>
  );
}
function SkillEditor({
  base,
  runId,
  nodeRef,
  busy,
  chain,
  onChange,
}: {
  base: string;
  runId: string;
  nodeRef: string;
  busy: boolean;
  chain: string[];
  onChange: () => void;
}) {
  const [path, setPath] = useState(
      "drugpaper/skills/builtin/visual_classify/SKILL.md",
    ),
    [content, setContent] = useState(""),
    [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false),
    [result, setResult] = useState<unknown>();
  const prefix = `${base}/workbench`;
  const list = useResource<{
    version: number;
    files: string[];
    changed: string[];
  }>(`${prefix}/files?node_ref=${enc(nodeRef)}`);
  const file = useResource<{
    version: number;
    content: string;
    baseline: string;
    local: boolean;
  }>(`${prefix}/files?node_ref=${enc(nodeRef)}&path=${enc(path)}`);
  const historical = useResource<{ content: string }>(
    runId ? `${prefix}/runs/${enc(runId)}/file?path=${enc(path)}` : null,
  );
  useEffect(() => {
    if (file.data) setContent(file.data.content);
  }, [file.data]);
  async function perform(action: string) {
    setSaving(true);
    setError("");
    try {
      setResult(
        await request(`${prefix}/${action}`, {
          method: action === "files" ? "PUT" : "POST",
          body: {
            node_ref: nodeRef,
            path,
            content,
            expected_version: file.data?.version,
          },
          timeout: 120000,
        }),
      );
      list.refresh();
      if (action === "files") file.refresh();
      onChange();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="card">
      <h2>局部 Skill / 工具代码</h2>
      <p className="muted">
        仅当前文献当前图表生效。先保存、校验，再使用单图执行试运行。
      </p>
      {chain.map((name) => (
        <button
          key={name}
          onClick={() => setPath(`drugpaper/skills/builtin/${name}/SKILL.md`)}
        >
          {name}
        </button>
      ))}
      <input
        aria-label="查找 Skill 文件"
        placeholder="查找文档、提示词、脚本、工具文件"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <select
        aria-label="Skill 文件"
        value={path}
        onChange={(e) => setPath(e.target.value)}
      >
        {list.data?.files
          .filter((p) => p.includes(query) || p === path)
          .map((p) => (
            <option key={p}>{p}</option>
          ))}
      </select>
      <label>
        文件路径（可新增包内文件）
        <input value={path} onChange={(e) => setPath(e.target.value)} />
      </label>
      <ResourceState {...file} />
      {historical.data && (
        <details>
          <summary>所选运行实际使用的文件快照</summary>
          <pre>{historical.data.content}</pre>
        </details>
      )}
      <textarea
        aria-label="Skill 内容"
        className="code-editor"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />
      <div className="toolbar">
        <button
          disabled={busy || saving || !file.data}
          onClick={() => void perform("files")}
        >
          保存当前图表局部版本
        </button>
        <button
          disabled={saving || busy}
          onClick={() => void perform("validate")}
        >
          校验已保存的包
        </button>
        <button
          disabled={saving || busy || !list.data?.changed.length}
          onClick={() => {
            if (
              window.confirm(
                "将已试运行的局部修改更新至全局，供后续其他图表使用？",
              )
            )
              void perform("publish");
          }}
        >
          主动更新全局
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {result !== undefined && <Raw value={result} />}
      <details>
        <summary>
          与全局基线比较 · {file.data?.local ? "当前有局部覆盖" : "沿用全局"}
        </summary>
        <div className="code-diff">
          <pre>{file.data?.baseline}</pre>
          <pre>{content}</pre>
        </div>
      </details>
    </section>
  );
}
