import { useEffect, useMemo, useRef, useState } from "react";
import { Raw } from "../ui";
import { downloadJSON } from "../debug.mjs";
import { ProcessContent } from "./process-content";
import {
  callImages,
  callStatus,
  callSummary,
  toolLabels,
  traceSteps,
  sourceLabels,
} from "../agent-trace.mjs";

type RecordData = Record<string, unknown>;
type Event = { kind: string; stage: string; at: string; data: unknown };
type Entry = RecordData & {
  kind: string;
  turn?: number;
  message?: string;
  at?: string;
  status?: string;
  reason?: string;
  name?: string;
  active_images?: number;
  request_bytes?: number;
};
type Call = {
  name: string;
  inputImages?: RecordData[];
  start: (Entry & { arguments?: RecordData }) | null;
  result:
    | (Entry & {
        arguments?: RecordData;
        result?: RecordData;
        duration_ms?: number;
      })
    | null;
};
type ProcessStep = {
  inputDelta?: { known: boolean; added: RecordData[]; total: number };
  type: string;
  sequence: number;
  entry?: Entry;
  decision?: Entry;
  call?: Call;
  children?: ProcessStep[];
  image_refs?: RecordData[];
};
type Evidence = {
  id: string;
  tool: string;
  arguments?: RecordData;
  result: RecordData;
};
type Image = {
  src: string;
  page: number | null;
  label: string;
  precision: string;
  viewed: boolean;
};
const statusLabels: Record<string, string> = {
  completed: "完成",
  running: "执行中",
  interrupted: "未收到返回",
  failed: "执行失败",
  blocked: "规则拒绝",
  needs_review: "需复核",
  passed: "通过",
  needs_revision: "需要修改",
  uncertain: "待确认",
  review_error: "审查请求失败",
};
const stopLabels: Record<string, string> = {
  agent_finished: "Agent 主动结束",
  no_progress: "多次尝试未取得新进展",
  tool_budget: "工具调用达到预算",
  turn_budget: "对话轮次达到预算",
  time_budget: "达到本次运行时间限制",
  model_error: "模型请求失败",
  context_budget: "上下文达到预算",
  cancelled: "运行已取消",
};
const toolName = (name: string) =>
  (toolLabels as Record<string, string>)[name] || name;

function JsonDetails({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{title}</summary>
      {open && <Raw value={value} />}
    </details>
  );
}
function EvidenceImage({
  image,
  onOpen,
}: {
  image: Image;
  onOpen: (image: Image) => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="agent-evidence-image">
      <button
        className="agent-image-button"
        onClick={() => onOpen(image)}
        aria-label={`放大查看${image.page ? ` PDF 第 ${image.page} 页` : "图像"}`}
      >
        {failed ? (
          <span>图像加载失败，点击打开查看</span>
        ) : (
          <img
            src={image.src}
            loading="lazy"
            onError={() => setFailed(true)}
            alt={`Agent ${image.precision === "preview" ? "页面概览" : image.viewed ? "工具返回的文献证据图" : "文字来源预览"}${image.page ? ` · PDF 第 ${image.page} 页` : ""}`}
          />
        )}
      </button>
      <figcaption>
        {image.page ? `PDF 第 ${image.page} 页 · ` : ""}
        {image.precision === "preview"
          ? "页面概览，需高清核对"
          : image.viewed
            ? "图像工具返回的证据"
            : "界面补充预览，Agent 本次读取的是文字"}
        {image.label && <small title={image.label}>{image.label}</small>}
      </figcaption>
    </figure>
  );
}
function InputImages({
  refs,
  onOpen,
  labels,
  title = "本次实际附带的图像",
}: {
  refs: RecordData[];
  onOpen: (image: Image) => void;
  labels: Record<string, string>;
  title?: string;
}) {
  const images = callImages({
    name: "context",
    result: { result: { ok: true, image_refs: refs } },
  }) as Image[];
  return (
    <div className="agent-input-images">
      <span>
        {title} · {images.length} 张
      </span>
      <div className="toolbar">
        {images.map((image, i) => (
          <button key={i} onClick={() => onOpen(image)}>
            {image.page ? `PDF 第 ${image.page} 页` : `图像 ${i + 1}`}
            {labels[image.label] ? ` · ${labels[image.label]}` : ""} · 查看
          </button>
        ))}
      </div>
    </div>
  );
}
function Review({
  step,
  onOpen,
  labels,
}: {
  step: ProcessStep;
  onOpen: (image: Image) => void;
  labels: Record<string, string>;
}) {
  const entry = step.entry || { kind: "structure_review" };
  const issues = (entry.issues || []) as {
    issue_id?: string;
    description?: string;
    field_path?: string;
    suggested_action?: string;
    assignment_index?: number;
    field?: string;
    source_evidence?: string;
  }[];
  const coverage = (entry.coverage || []) as {
    verdict?: string;
    evidence?: string;
    dimension?: string;
  }[];
  return (
    <div className="agent-review-body">
      <p>
        <strong>
          {statusLabels[entry.status || ""] || entry.status || "审核结果已返回"}
        </strong>
        {entry.review_id ? ` · ${entry.review_id}` : ""}
      </p>
      {entry.reused === true && (
        <p>结构未变化，复用已有审核结论，本步没有重新调用审核模型。</p>
      )}
      {!!issues.length && (
        <ul className="agent-review-issues">
          {issues.map((issue, i) => (
            <li key={i}>
              <p>{issue.description}</p>
              {typeof issue.assignment_index === "number" && (
                <p>
                  提案成员第 {issue.assignment_index + 1} 项 · {issue.field}
                </p>
              )}
              {issue.source_evidence && <p>{issue.source_evidence}</p>}
              {issue.field_path && <code>{issue.field_path}</code>}
              {issue.suggested_action && (
                <p>建议的下一步：{issue.suggested_action}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      {!issues.length &&
        coverage
          .filter((c) => c.verdict !== "pass")
          .map((c, i) => <p key={i}>{c.evidence}</p>)}
      {!!step.image_refs?.length && (
        <InputImages
          refs={step.image_refs}
          labels={labels}
          onOpen={onOpen}
          title="审核实际参考的图像"
        />
      )}
      <JsonDetails title="完整审核意见与字段" value={entry} />
    </div>
  );
}
function ChangeList({ value }: { value: RecordData }) {
  const patch = (value.patch || value.record || {}) as RecordData;
  const changes = (patch.changes || []) as {
    path: string;
    before?: unknown;
    after?: unknown;
  }[];
  const operations = (patch.operations || []) as {
    path: string;
    op?: string;
    value?: unknown;
  }[];
  const rows = changes.length
    ? changes
    : operations.map((o) => ({
        path: o.path,
        before: undefined,
        after: o.value,
      }));
  const brief = (v: unknown) =>
    v === undefined
      ? "未记录"
      : v === null
        ? "空"
        : Array.isArray(v)
          ? `${v.length} 项（展开 JSON 查看内容）`
          : typeof v === "object"
            ? "对象（展开 JSON 查看内容）"
            : String(v);
  if (!rows.length) return null;
  return (
    <div className="agent-changes">
      <strong>
        {value.dry_run ? "预计修改的字段（尚未应用）" : "本次修改的字段"}
      </strong>
      <ul>
        {rows.map((c, i) => (
          <li key={i}>
            <code>{c.path}</code>
            <p>
              {brief(c.before)} → {brief(c.after)}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
function ToolOperation({
  step,
  running,
  evidence,
  labels,
  base,
  onOpen,
}: {
  step: ProcessStep;
  running: boolean;
  evidence: Evidence[];
  labels: Record<string, string>;
  base: string;
  onOpen: (im: Image) => void;
}) {
  const call = step.call!;
  const args = call.start?.arguments || call.result?.arguments || {};
  const result = (call.result?.result || {}) as RecordData;
  const data = (result.data || result) as RecordData;
  const status = !call.result && !running ? "interrupted" : callStatus(call);
  const images = (callImages(call, evidence, base) as Image[]).map((im) => ({
    ...im,
    label: labels[im.label] || im.label,
  }));
  return (
    <>
      <div className="agent-operation-meta">
        <span className={`agent-status agent-status-${status}`}>
          {statusLabels[status] || status}
        </span>
        {call.result?.duration_ms !== undefined && (
          <small>{(call.result.duration_ms / 1000).toFixed(1)} 秒</small>
        )}
      </div>
      {typeof args.reason === "string" && (
        <p className="agent-tool-purpose">
          <span>目的</span>
          {args.reason}
        </p>
      )}
      {call.inputImages?.length ? (
        <InputImages refs={call.inputImages} labels={labels} onOpen={onOpen} />
      ) : null}
      <section className="agent-call-input">
        <h5>调用输入</h5>
        <ProcessContent value={args} images={false} />
      </section>
      <section className="agent-call-output">
        <h5>返回内容</h5>
        {call.result ? (
          <ProcessContent value={result} images={false} />
        ) : (
          <p>{running ? "等待工具返回…" : "未记录到返回结果。"}</p>
        )}
      </section>
      {!!images.length && (
        <div className="agent-evidence-gallery">
          {images.map((im, i) => (
            <EvidenceImage key={i} image={im} onOpen={onOpen} />
          ))}
        </div>
      )}
      <ChangeList value={data} />
      {data.repair && typeof data.repair === "object" ? (
        <JsonDetails title="具体字段错误与修正要求" value={data.repair} />
      ) : null}
      {data.regeneration && typeof data.regeneration === "object" ? (
        <div className="agent-changes">
          <strong>整体重建 · 旧版本已保留</strong>
          <p>
            版本 {String((data.regeneration as RecordData).base_version)} →{" "}
            {String((data.regeneration as RecordData).new_version)}
          </p>
          <p>{String((data.regeneration as RecordData).note || "")}</p>
          <JsonDetails title="重建依据与版本记录" value={data.regeneration} />
        </div>
      ) : null}
      {!!step.children?.length && (
        <div className="agent-operation-children">
          {step.children.map((child, i) => (
            <section key={i}>
              <h5>
                {child.entry?.kind === "reference_review"
                  ? "核对参考图中的成员赋值"
                  : child.entry?.reused
                    ? "复用已有审核结论"
                    : "执行期间的独立审核"}
              </h5>
              <Review step={child} labels={labels} onOpen={onOpen} />
            </section>
          ))}
        </div>
      )}
      <p className="agent-tool-outcome">
        <span>返回</span>
        {status === "interrupted"
          ? "没有记录到返回结果；任务已结束或中断。"
          : callSummary(call)}
      </p>
      <JsonDetails
        title={`原始参数与返回 · ${call.name}`}
        value={{ arguments: args, result: call.result?.result ?? null }}
      />
    </>
  );
}
export function AgentTimeline({
  events,
  exploration,
  base,
  running = false,
}: {
  events: Event[];
  exploration?: RecordData;
  base: string;
  running?: boolean;
}) {
  const records = useMemo(
    () => events.filter((e) => e.stage === "structure"),
    [events],
  );
  const steps = useMemo(
    () =>
      traceSteps(
        records,
        (exploration?.steps || []) as Entry[],
      ) as ProcessStep[],
    [records, exploration?.steps],
  );
  const labels = useMemo(
    () =>
      sourceLabels(records, exploration?.document_map || {}) as Record<
        string,
        string
      >,
    [records, exploration?.document_map],
  );
  const evidence = (exploration?.evidence || []) as Evidence[];
  const calls = steps.filter((s) => s.type === "tool").map((s) => s.call!);
  const seenIds = new Set(
    calls.map((c) => c.result?.result?.evidence_id).filter(Boolean),
  );
  const inherited = evidence.filter((e) => !seenIds.has(e.id));
  const [image, setImage] = useState<Image | null>(null),
    [zoom, setZoom] = useState(100);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (image) {
      setZoom(100);
      dialog.current?.showModal();
    } else dialog.current?.close();
  }, [image]);
  const title = (step: ProcessStep) =>
    step.type === "tool"
      ? toolName(step.call!.name)
      : step.type === "review"
        ? step.entry?.kind === "reference_review"
          ? "核对参考图中的成员赋值"
          : step.entry?.reused
            ? "复用已有审核结论"
            : "独立审核结构化结果"
        : step.type === "agent"
          ? "Agent 选择下一步操作"
          : step.type === "extraction"
            ? "首次结构化提取"
            : step.type === "structure_resumed"
              ? "载入已有结构化结果"
              : step.type === "stopped"
                ? "本次处理结束"
                : step.type === "evidence_restored"
                  ? "恢复已保存的参考图"
                  : step.type;
  return (
    <div className="agent-timeline">
      <p className="agent-trace-intro">
        按实际操作顺序回放。图像仅附在获取它的步骤中；每次模型请求使用的参考图可在对应操作中查看。
      </p>
      <div className="toolbar">
        <button
          onClick={() =>
            downloadJSON("figure-agent-process.json", {
              events: records,
              exploration,
            })
          }
        >
          导出完整操作记录
        </button>
      </div>
      {!steps.length && (
        <p className="empty-state">
          尚无操作记录，开始处理后将在这里逐步显示。
        </p>
      )}
      <ol className="agent-process-list" aria-label="Agent 逐步操作记录">
        {steps.map((step, index) => {
          const entry = step.entry || { kind: step.type };
          const contextRefs = (entry.image_refs || []) as RecordData[];
          const tools = (step.decision?.tools || []) as string[];
          const status = step.call ? callStatus(step.call) : entry.status;
          return (
            <li
              className={`agent-process-step agent-status-${status || "neutral"}`}
              key={`${step.sequence}-${step.type}`}
            >
              <header>
                <span className="agent-step-number">{index + 1}</span>
                <h4>
                  {typeof (step.call?.start?.turn ?? entry.turn) === "number"
                    ? `第 ${step.call?.start?.turn ?? entry.turn} 轮 · `
                    : ""}
                  {title(step)}
                </h4>
              </header>
              {step.type === "tool" ? (
                <ToolOperation
                  step={step}
                  running={running}
                  evidence={evidence}
                  labels={labels}
                  base={base}
                  onOpen={setImage}
                />
              ) : step.type === "review" ? (
                <Review step={step} labels={labels} onOpen={setImage} />
              ) : step.type === "agent" ? (
                <>
                  {entry.review_id ? (
                    <p>本次请求参考审核记录 {String(entry.review_id)}。</p>
                  ) : null}
                  {entry.workflow && typeof entry.workflow === "object" ? (
                    <JsonDetails
                      title="当前待完成工作与剩余预算"
                      value={entry.workflow}
                    />
                  ) : null}
                  {contextRefs.length ? (
                    <>
                      {step.inputDelta?.added.length ? (
                        <InputImages
                          refs={step.inputDelta.added}
                          labels={labels}
                          onOpen={setImage}
                          title={
                            step.inputDelta.known
                              ? "本次新增的参考图像"
                              : "本次实际附带的图像"
                          }
                        />
                      ) : (
                        <p className="agent-trace-muted">
                          沿用已提供的 {contextRefs.length}{" "}
                          张图像，本次未新增图片。
                        </p>
                      )}
                      <details>
                        <summary>
                          完整输入图像清单 · {contextRefs.length} 张
                        </summary>
                        <InputImages
                          refs={contextRefs}
                          labels={labels}
                          onOpen={setImage}
                        />
                      </details>
                    </>
                  ) : (
                    <p className="agent-trace-muted">
                      {entry.active_images !== undefined
                        ? `记录显示请求包含 ${entry.active_images} 张图像，但未保存可对应的图像清单。`
                        : "此历史记录没有保存请求图像清单。"}
                    </p>
                  )}
                  {step.decision ? (
                    <p className="agent-decision-text">
                      {step.decision.message ||
                        (tools.length
                          ? `选择调用：${tools.map(toolName).join(" → ")}。具体目的与返回见后续步骤。`
                          : "未记录文字回复或工具选择。")}
                    </p>
                  ) : (
                    <p>
                      {running
                        ? "正在等待模型返回后续操作…"
                        : "未记录到本次模型回复。"}
                    </p>
                  )}
                  <JsonDetails
                    title="本次模型交互的原始记录"
                    value={{ request: entry, response: step.decision }}
                  />
                </>
              ) : step.type === "extraction" ? (
                <>
                  {!!step.image_refs?.length && (
                    <InputImages
                      refs={step.image_refs}
                      labels={labels}
                      onOpen={setImage}
                      title="结构化提取实际输入的图像"
                    />
                  )}
                  <p>结构化请求已记录，提取结果与后续审核分开保留。</p>
                  <JsonDetails title="查看结构化请求记录" value={entry} />
                </>
              ) : step.type === "evidence_restored" ? (
                <>
                  <p>
                    重新附上已保存图像，属于上下文恢复，没有重新调用图像查阅工具。
                  </p>
                  <InputImages
                    refs={contextRefs}
                    labels={labels}
                    onOpen={setImage}
                    title="本次恢复的参考图"
                  />
                  <JsonDetails title="恢复清单与未加载项" value={entry} />
                </>
              ) : (
                <>
                  <p>
                    {statusLabels[entry.status || ""] || entry.status || ""}{" "}
                    {stopLabels[entry.reason || ""] ||
                      entry.reason ||
                      (step.type === "structure_resumed"
                        ? "沿用已有 JSON，接着审查和局部修正，没有重新提取。"
                        : "")}
                  </p>
                  <JsonDetails title="原始记录" value={entry} />
                  {step.type === "stopped" && entry.unfinished_work ? (
                    <JsonDetails
                      title="未完成工作与交接清单"
                      value={entry.unfinished_work}
                    />
                  ) : null}
                </>
              )}
            </li>
          );
        })}
      </ol>
      {Array.isArray(exploration?.structure_versions) &&
        exploration.structure_versions.length > 0 && (
          <JsonDetails
            title={`重建前的历史版本 · ${exploration.structure_versions.length} 份`}
            value={exploration.structure_versions}
          />
        )}
      {!!inherited.length && (
        <details className="agent-inherited">
          <summary>
            没有对应本次操作事件的已保存资料 · {inherited.length} 项
          </summary>
          <p>
            可能继承自历史任务。缺少顺序记录，因此不插入上方操作链，也不宣称是本次新查阅的内容。
          </p>
          {inherited.map((e) => (
            <div key={e.id}>
              <h4>
                {e.id} · {toolName(e.tool)}
              </h4>
              <JsonDetails title="查看保存的内容与来源" value={e} />
            </div>
          ))}
        </details>
      )}
      <dialog
        ref={dialog}
        className="agent-image-dialog"
        onCancel={() => setImage(null)}
      >
        <header>
          <strong>
            {image?.page ? `PDF 第 ${image.page} 页` : "记录中的图像"}
          </strong>
          <div className="toolbar">
            <button onClick={() => setZoom((z) => Math.max(50, z - 25))}>
              缩小
            </button>
            <span>{zoom}%</span>
            <button onClick={() => setZoom((z) => Math.min(300, z + 25))}>
              放大
            </button>
            <a href={image?.src} target="_blank" rel="noreferrer">
              打开原图
            </a>
            <button onClick={() => setImage(null)}>关闭图像</button>
          </div>
        </header>
        <div className="agent-image-viewport">
          {image && (
            <img
              src={image.src}
              alt="放大的文献证据图"
              style={{ width: `${zoom}%` }}
            />
          )}
        </div>
      </dialog>
    </div>
  );
}
