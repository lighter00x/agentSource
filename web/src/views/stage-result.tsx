import type { ReactNode } from "react";
import { Raw } from "../ui";
import { redact } from "../debug.mjs";
import { Molecule } from "./molecule";

type Data = Record<string, unknown>;
type Event = { kind: string; data: unknown };
export type StageStatus = {
  status: string;
  source_run?: string;
  error?: string;
  duration_ms?: number;
};
const obj = (v: unknown): Data =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Data) : {};
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
export const statusLabel = (v: string) =>
  ({
    completed: "已完成",
    reused: "沿用上游",
    running: "执行中",
    queued: "排队中",
    paused: "已暂停",
    failed: "失败",
    skipped: "已跳过",
    ok: "通过",
    needs_review: "待复核",
    remediated: "已修正",
    correct: "正确",
    incorrect: "有误",
    unsure: "不确定",
    error: "错误",
    warning: "警告",
  })[v] || v;
const types: Record<string, string> = {
  synthetic_scheme_single: "单条合成路线",
  synthetic_scheme_multi: "多条合成路线",
  sar_table: "构效关系表",
  data_table: "数据表格",
  reported_structures: "分子结构集合",
  molecular_docking: "分子对接",
  protac_architecture: "PROTAC 结构",
  metabolic_pathway: "代谢通路",
  pharmacophore_model: "药效团模型",
  property_plot: "性质曲线",
  data_chart: "数据图表",
  multi_panel: "多面板图",
  other: "其他图表",
};
const skills: Record<string, string> = {
  visual_classify: "判断图像类型",
  visual_reclassify: "重新判断图像类型",
  molecule_detect: "定位分子结构",
  molecule_recognize: "识别分子结构",
  som_render: "生成分子编号图",
  label_bind: "关联分子与原图编号",
  scheme_extract: "提取合成路线",
  table_extract: "提取表格数据",
  structures_extract: "提取结构集合",
  mol_backfill: "回填分子信息",
  measurement_map: "映射测定指标",
  sar_record: "整理构效关系记录",
  chart_digitize: "提取图表数值",
  panel_split: "拆分面板",
  common_extract: "通用图表提取",
};
const explanations: Record<string, string> = {
  classify:
    "根据原图判断图表类型及化学结构特征，为后续处理选择合适的路线。置信度是模型的判断信心，不等同于结果正确率。",
  plan: "根据图表类型选择 Skill，并按下面的顺序执行。此处显示计划，实际调用见下方记录。",
  detect:
    "定位原图中的分子结构区域，供后续逐个识别。检测到区域并不表示已成功识别化学结构。",
  recognize:
    "将分子区域识别为 SMILES 等化学结构信息。下方展示本步骤保存的识别结果。",
  som: "在图像上标注分子编号，让结构化提取能够引用对应的分子区域。编号图是模型输入材料。",
  structure:
    "结合图像、分子编号和参考证据提取结构化内容。详细决策与审查过程见下方 Agent 探索过程。",
  post: "将分子引用回填为化学结构，并将提取的数据映射为测定指标或构效关系记录。",
  check_l1:
    "用确定性规则检查结构、数值与引用的一致性。问题列表仅表示规则发现的风险。",
  check_l2: "由模型核对待审查字段，分别给出正确、有误或不确定的结论及理由。",
  report: "汇总提取结果、检查问题与未解决事项，生成此图的最终质量结论。",
  deliver:
    "将详细结果整理为便于阅读和使用的过程结果，完整化学信息可从最终解析结果下载。",
};
const labels: Record<string, string> = {
  boxes: "分子区域",
  structures: "分子结构",
  local_bbox: "区域坐标（相对原图）",
  parent_region_index: "图像片段序号",
  reading_index: "分子编号",
  confidence: "置信度",
  validation: "结构校验",
  valid: "有效",
  error: "错误",
  evidence: "证据",
  provenance: "来源记录",
  whole_image: "整图检测",
  image_refs: "图像引用",
  local_id: "区域标识",
  structure_id: "结构标识",
  is_markush: "含通式结构",
  is_scaffold_candidate: "候选骨架",
  masked_regions: "屏蔽区域",
  cls: "检测类别",
  inchikey: "InChIKey",
  segment: "图像片段",
  items: "条目",
  status: "状态",
  total: "总数",
  detected_count: "检测到的分子区域数",
  resolved_count: "成功回填数",
  excluded_count: "排除数",
  complete_for_detected_boxes: "已覆盖检测到的分子区域",
  scope: "覆盖范围说明",
  resolved: "已解析",
  unresolved: "未解析",
  covered: "已覆盖",
  missing: "缺失",
  coverage: "覆盖情况",
  molecule_count: "分子数",
  mol_id: "分子编号",
  node_ref: "节点引用",
  formula: "分子式",
  canonical_smiles: "标准 SMILES",
  smiles: "SMILES",
  smiles_core: "骨架 SMILES",
  e_smiles_raw: "原始扩展 SMILES",
  field_path: "字段位置",
  message: "说明",
  reason: "原因",
  code: "检查代码",
  dimension: "检查维度",
  severity: "严重程度",
  verdict: "复核结论",
  suggested_value: "建议值",
  description: "说明",
  label: "标签",
  reference: "引用",
  name: "名称",
  count: "数量",
  source: "来源",
  note: "备注",
  errors: "错误",
  warnings: "警告",
  open_questions: "未解决事项",
  structure: "结构",
  numeric: "数值",
  chart: "图表",
  content: "内容",
  expected: "预期",
  observed: "实际",
  recognized: "已识别",
  failed: "失败",
  complete: "完整",
  partial: "部分完成",
};
function scalar(v: unknown): string {
  if (v == null) return "未提供";
  if (typeof v === "boolean") return v ? "是" : "否";
  const s = String(v);
  return labels[s] || statusLabel(s);
}
// Readable field views retain unknown fields rather than silently dropping new schema data.
function Fields({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (Array.isArray(value))
    return value.length ? (
      <ul className="stage-items">
        {value.map((v, i) => (
          <li key={i}>
            <Fields value={v} depth={depth + 1} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="muted">无记录</span>
    );
  if (value && typeof value === "object") {
    const data = obj(value),
      smiles = data.canonical_smiles || data.smiles_core;
    if (typeof smiles === "string" && smiles && depth < 3)
      return (
        <div className="stage-molecule">
          <Molecule smiles={smiles} />
          <Fields
            value={Object.fromEntries(
              Object.entries(data).filter(
                ([k]) => !["canonical_smiles", "smiles_core"].includes(k),
              ),
            )}
            depth={3}
          />
          <p>
            <code>{smiles}</code>
          </p>
        </div>
      );
    if (depth > 3)
      return (
        <details>
          <summary>展开详细字段</summary>
          <Fields value={value} />
        </details>
      );
    return (
      <dl className="stage-fields">
        {Object.entries(obj(value)).map(([k, v]) => (
          <div key={k}>
            <dt>{labels[k] || k}</dt>
            <dd>
              <Fields value={v} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span>{scalar(value)}</span>;
}
function Block({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="stage-block">
      <h4>{title}</h4>
      {value === undefined ? (
        <p className="muted">此步骤未保存该项数据。</p>
      ) : (
        <Fields value={redact(value)} />
      )}
    </section>
  );
}
function Facts({ entries }: { entries: [string, unknown][] }) {
  return (
    <dl className="stage-facts">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{scalar(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
function Inventory({ value }: { value: unknown }) {
  const data = obj(value);
  const entries = Object.entries(data).filter(([, v]) => Array.isArray(v));
  return entries.length ? (
    <div className="stage-facts">
      {entries.map(([k, v]) => (
        <div key={k}>
          <span>
            {(
              {
                molecule_inventory: "分子清单",
                molecule_objects: "分子对象",
                compound_instances: "化合物实例",
                relations: "关系",
                pathways: "路线",
                rows: "数据行",
                panels: "面板",
                items: "条目",
                steps: "步骤",
                measurements: "测定记录",
              } as Record<string, string>
            )[k] || k}
          </span>
          <strong>{list(v).length}</strong>
        </div>
      ))}
    </div>
  ) : (
    <p className="muted">尚无可统计的结果条目。</p>
  );
}
export function StageResult({
  stage,
  label,
  state,
  events,
  status,
  children,
}: {
  stage: string;
  label: string;
  state?: Data;
  events: Event[];
  status?: StageStatus;
  children?: ReactNode;
}) {
  const s = state || {},
    c = obj(s.classification),
    material = obj(s.material);
  const results = events
    .filter((e) => e.kind === "skill_result")
    .map((e) => obj(e.data));
  let content: ReactNode;
  if (stage === "classify")
    content = (
      <>
        <Facts
          entries={[
            ["图表类型", types[String(c.image_type)] || c.image_type],
            [
              "判断置信度",
              typeof c.confidence === "number"
                ? `${Math.round(c.confidence * 100)}%`
                : undefined,
            ],
            ["包含化学结构", c.has_chemical_structures],
            ["包含 Markush 骨架", c.has_markush_scaffold],
            ["面板数量", c.panel_count],
            ["需要人工复核", c.needs_review],
            ["人工指定类型", c.manual],
          ]}
        />
        <Block title="判断依据（模型原文）" value={c.evidence} />
      </>
    );
  else if (stage === "plan")
    content = (
      <>
        <Facts
          entries={[
            ["依据图表类型", types[String(c.image_type)] || c.image_type],
          ]}
        />
        {list(s.chain).length ? (
          <ol className="stage-route">
            {list(s.chain).map((name, i) => (
              <li key={i}>
                <strong>{skills[String(name)] || String(name)}</strong>
                <code>{String(name)}</code>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">尚未保存 Skill 路线。</p>
        )}
      </>
    );
  else if (stage === "detect" || stage === "recognize" || stage === "som") {
    const names =
      stage === "detect"
        ? ["molecule_detect"]
        : stage === "recognize"
          ? ["molecule_recognize"]
          : ["som_render", "label_bind"];
    const selected = results.filter((r) => names.includes(String(r.name)));
    const boxes = selected.length
      ? selected.flatMap((r) => list(obj(r.payload).boxes))
      : list(material.boxes).map((v) => ({
          ...obj(v),
          local_bbox: obj(v).bbox,
          parent_region_index: obj(v).segment,
          reading_index: obj(v).mol_id,
        }));
    content = (
      <>
        {stage === "detect" && (
          <Facts
            entries={[
              [
                "检测到的分子区域",
                selected.length || material.boxes !== undefined
                  ? boxes.length
                  : undefined,
              ],
            ]}
          />
        )}
        {stage === "recognize" && (
          <details>
            <summary>识别结果汇总</summary>
            {selected.length ? (
              selected.map((r, i) => (
                <Block
                  key={i}
                  title="识别出的分子结构"
                  value={obj(r.payload).structures}
                />
              ))
            ) : (
              <Block title="保存的分子结构" value={material.structures} />
            )}
          </details>
        )}
        {list(s.image_products)
          .filter((v) =>
            stage === "som"
              ? obj(v).stage === "model_input_numbered"
              : stage === "detect" && obj(v).stage === "original",
          )
          .map((v, i) => {
            const im = obj(v);
            return typeof im.blob_ref === "string" ? (
              <figure className="stage-image" key={i}>
                <a
                  href={`/api/v1/blobs/${encodeURIComponent(im.blob_ref)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <div className="stage-image-canvas">
                    <img
                      loading="lazy"
                      src={`/api/v1/blobs/${encodeURIComponent(im.blob_ref)}`}
                      alt={stage === "som" ? "分子编号图" : "检测来源原图"}
                    />
                    {stage === "detect" &&
                      boxes
                        .filter(
                          (v) => obj(v).parent_region_index === im.segment,
                        )
                        .map((v, j) => {
                          const box = obj(v),
                            b = obj(box.local_bbox);
                          if (
                            ![b.x0, b.y0, b.x1, b.y1].every(
                              (n) => typeof n === "number",
                            )
                          )
                            return null;
                          return (
                            <span
                              className="stage-detection-box"
                              key={j}
                              style={{
                                left: `${Number(b.x0) * 100}%`,
                                top: `${Number(b.y0) * 100}%`,
                                width: `${(Number(b.x1) - Number(b.x0)) * 100}%`,
                                height: `${(Number(b.y1) - Number(b.y0)) * 100}%`,
                              }}
                            >
                              <b>{scalar(box.reading_index)}</b>
                            </span>
                          );
                        })}
                  </div>
                </a>
                <figcaption>
                  {stage === "som" ? "模型输入编号图" : "检测来源原图"} ·{" "}
                  {typeof im.page === "number"
                    ? `PDF 第 ${im.page + 1} 页`
                    : "页码未记录"}{" "}
                  · 点击查看大图
                </figcaption>
              </figure>
            ) : null;
          })}
        {stage !== "recognize" && (
          <details>
            <summary>
              {stage === "detect" ? "分子区域与检测详情" : "编号与标签关联详情"}
            </summary>
            {selected.length ? (
              selected.map((r, i) => (
                <Block
                  key={i}
                  title={skills[String(r.name)] || String(r.name)}
                  value={r.payload}
                />
              ))
            ) : (
              <Block title="保存的分子区域" value={material.boxes} />
            )}
          </details>
        )}
      </>
    );
  } else if (stage === "check_l1")
    content = (
      <>
        <Facts
          entries={[
            [
              "规则发现的问题",
              Array.isArray(s.checks) ? s.checks.length : undefined,
            ],
            [
              "待模型核对字段",
              Array.isArray(s.checklist) ? s.checklist.length : undefined,
            ],
            [
              "未检查项",
              Array.isArray(s.unchecked) ? s.unchecked.length : undefined,
            ],
          ]}
        />
        <Block title="规则检查问题" value={s.checks} />
        <Block title="待模型核对字段" value={s.checklist} />
        <Block title="未检查项" value={s.unchecked} />
      </>
    );
  else if (stage === "check_l2")
    content = <Block title="逐项复核结论与理由" value={obj(s.review).items} />;
  else if (stage === "post")
    content = (
      <>
        <Inventory value={s.backfilled_extraction} />
        <details>
          <summary>查看回填后的分子与关系</summary>
          <Block title="回填结果" value={s.backfilled_extraction} />
        </details>
        <Block title="分子覆盖情况" value={s.molecular_coverage} />
        <Block title="回填问题" value={material.backfill_errors} />
        <Block title="映射后的测定记录" value={material.measurements} />
      </>
    );
  else if (stage === "report") {
    const report = obj(s.report);
    content = (
      <>
        <Facts
          entries={[
            ["质量结论", report.status],
            ["图表类型", types[String(report.image_type)] || report.image_type],
          ]}
        />
        <Inventory value={report.backfilled_extraction || report.extraction} />
        <Block title="未解决事项" value={report.open_questions} />
        <Block title="检查发现的问题" value={report.checks} />
        <Block title="未检查项" value={report.unchecked} />
      </>
    );
  } else if (stage === "deliver")
    content = (
      <>
        <Facts entries={[["结果状态", obj(s.summary_result).status]]} />
        <Inventory value={s.summary_result} />
        <Block title="精简过程结果" value={s.summary_result} />
      </>
    );
  else content = <Inventory value={s.extraction} />;
  return (
    <section className="stage-result" aria-label={`${label}结果`}>
      <div className="stage-result-heading">
        <h3>{label}</h3>
        <span className={`stage-status ${status?.status || "pending"}`}>
          {status ? statusLabel(status.status) : "未执行"}
        </span>
      </div>
      <p className="stage-explanation">
        {explanations[stage] || "查看此步骤保存的处理结果。"}
      </p>
      {status?.source_run && (
        <p className="stage-reuse">
          沿用运行 <code>{status.source_run}</code>{" "}
          的结果；本次未重新执行此步骤。
        </p>
      )}
      {status?.duration_ms !== undefined && (
        <p className="muted">本步骤耗时 {status.duration_ms} ms</p>
      )}
      {status?.error && (
        <p role="alert" className="error">
          {status.error}
        </p>
      )}
      {children}
      <h4>步骤结果概览</h4>
      {state || results.length ? (
        content
      ) : (
        <p className="muted">
          {status?.status === "running"
            ? "正在执行，结果尚未保存。"
            : "此步骤暂无保存的结果。"}
        </p>
      )}
      {!!results.length && (
        <details className="stage-calls">
          <summary>本步骤实际 Skill 调用 · {results.length} 次</summary>
          <ol>
            {results.map((r, i) => {
              const trace = obj(r.trace);
              return (
                <li key={i}>
                  <strong>{skills[String(r.name)] || String(r.name)}</strong>{" "}
                  <code>{String(r.name)}</code>
                  <p>
                    {trace.status
                      ? statusLabel(String(trace.status))
                      : "已保存调用结果"}{" "}
                    ·{" "}
                    {trace.cached === true
                      ? "使用缓存结果"
                      : trace.cached === false
                        ? "执行调用"
                        : "缓存状态未记录"}
                    {typeof trace.model_calls === "number" &&
                      ` · 模型调用 ${trace.model_calls} 次`}
                  </p>
                </li>
              );
            })}
          </ol>
        </details>
      )}
      {state && (
        <details>
          <summary>此步骤完成后的状态与结果 · 原始 JSON</summary>
          <Raw value={state} />
        </details>
      )}
    </section>
  );
}
