import { useState } from "react";
import { exportJSON } from "../debug.mjs";
import { Raw } from "../ui";

const names: Record<string, string> = {
  query: "查询内容",
  queries: "查询列表",
  reason: "调用目的",
  text: "文字内容",
  text_raw: "原文",
  text_norm: "规范化文字",
  snippet: "命中片段",
  content: "内容",
  data: "返回数据",
  result: "结果",
  results: "结果列表",
  items: "条目",
  hits: "搜索命中",
  matches: "匹配结果",
  nodes: "文献节点",
  candidates: "候选来源",
  page: "页码（从 0 起）",
  pages: "页码列表（从 0 起）",
  node_ref: "节点引用",
  node_id: "节点标识",
  label: "标签",
  name: "名称",
  arguments: "调用参数",
  error: "错误",
  status: "状态",
  ok: "是否成功",
  message: "说明",
  summary: "摘要",
  confidence: "置信度",
  evidence: "依据",
  structures: "分子结构",
  boxes: "分子区域",
  formula: "分子式",
  canonical_smiles: "标准 SMILES",
  image_type: "图表类型",
  has_chemical_structures: "包含化学结构",
  has_markush_scaffold: "包含 Markush 骨架",
  panel_count: "面板数量",
  needs_review: "需要复核",
  field_path: "字段位置",
  verdict: "结论",
  suggested_value: "建议值",
  tools: "可用工具",
  description: "说明",
  target: "处理目标",
  inputs: "输入内容",
  payload: "输出内容",
  tool_calls: "模型选择的工具",
  id: "标识",
  usage: "用量",
  input_tokens: "输入 token",
  output_tokens: "输出 token",
  image_count: "图像数",
  latency_ms: "耗时（毫秒）",
  model: "模型",
  validation: "结构校验",
  valid: "有效",
  turn: "轮次",
  request: "请求",
  response: "回答",
  repair: "修正要求",
  changes: "修改",
  before: "修改前",
  after: "修改后",
  record: "登记内容",
  patch: "补丁",
  source: "来源",
  scope: "范围",
  complete: "完整",
  incorrect: "有误",
  correct: "正确",
  unsure: "不确定",
  completed: "已完成",
  failed: "失败",
};
function Content({
  value,
  images = true,
}: {
  value: unknown;
  images?: boolean;
}) {
  if (value == null) return <span className="muted">未提供</span>;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object")
        return <Content value={parsed} images={images} />;
    } catch {
      /* Plain text remains text, including malformed JSON. */
    }
    return <span className="process-text">{value}</span>;
  }
  if (typeof value === "boolean") return <span>{value ? "是" : "否"}</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ol className="process-content-list">
        {value.map((v, i) => (
          <li key={i}>
            <Content value={v} images={images} />
          </li>
        ))}
      </ol>
    ) : (
      <span className="muted">无条目</span>
    );
  if (typeof value === "object") {
    const d = value as Record<string, unknown>;
    if (
      typeof d.blob_ref === "string" &&
      (!d.mime || String(d.mime).startsWith("image/"))
    )
      return (
        <figure className="process-content-image">
          <a
            href={`/api/v1/blobs/${encodeURIComponent(d.blob_ref)}`}
            target="_blank"
            rel="noreferrer"
          >
            {images ? (
              <img
                loading="lazy"
                src={`/api/v1/blobs/${encodeURIComponent(d.blob_ref)}`}
                alt="调用中的图像"
              />
            ) : (
              <span>{String(d.label || d.blob_ref)}</span>
            )}
          </a>
          <figcaption>
            {typeof d.page === "number" ? `PDF 第 ${d.page + 1} 页 · ` : ""}
            点击查看原图
          </figcaption>
        </figure>
      );
    if (!Object.keys(d).length) return <span className="muted">无字段</span>;
    return (
      <dl className="process-content-fields">
        {Object.entries(d).map(([key, v]) => (
          <div key={key}>
            <dt title={key}>{names[key] || key}</dt>
            <dd>
              <Content value={v} images={images} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
}
// Use the complete export sanitizer: the readable view must not truncate lists
// at 200 entries or text at 16,000 characters as the debug preview does.
export function ProcessContent({
  value,
  images = true,
}: {
  value: unknown;
  images?: boolean;
}) {
  const safe = value === undefined ? undefined : JSON.parse(exportJSON(value));
  return (
    <div className="process-content">
      <Content value={safe} images={images} />
    </div>
  );
}
export function ProcessJSON({
  title,
  value,
}: {
  title: string;
  value: unknown;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="process-json"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>{title}</summary>
      {open && <Raw value={value} />}
    </details>
  );
}
