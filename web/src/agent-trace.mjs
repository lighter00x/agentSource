// Replay public Agent events; never infer hidden reasoning or fabricate visual reads.
export const toolLabels = {
  get_investigation_plan: "制定查找计划",
  get_document_map: "浏览文献地图",
  search_text: "全文搜索",
  grep: "全文关键词搜索",
  get_context_window: "读取上下文",
  get_section: "读取章节",
  get_outline: "读取文献提纲",
  get_children: "读取子节点",
  get_page_text: "读取页面文字",
  get_node: "读取节点内容",
  find_reference_visuals: "定位相关图表",
  preview_pages: "浏览页面概览",
  inspect_visual: "查看图表高清图",
  inspect_page: "查看整页",
  inspect_crop: "查看局部图像",
  extract_figure: "首次结构化提取",
  review_structure: "独立审查",
  patch_structure: "局部修改结构化结果",
  get_structure: "读取结构化片段",
  get_workflow_status: "查看待完成工作",
  defer_edit: "撤销或登记未完成编辑",
  preview_reference_assignments: "预检成员赋值（未应用）",
  preview_structure_patch: "预检局部补丁（未应用）",
  regenerate_structure: "整体重建结构化结果",
  record_reference_assignments: "登记成员与取代信息",
  inspect_assembly: "查询拼接能力",
  try_assembly: "试拼分子",
  apply_assembly: "应用拼接修正",
  validate_result: "校验当前结果",
  update_gap: "更新信息缺口",
  finish: "结束本次探索",
  repair_markush_cases: "修正通式组合",
  select_evidence: "选择上下文证据",
};
const object = (v) =>
  v && typeof v === "object" && !Array.isArray(v) ? v : {};
const payload = (v) => object(object(v).data ?? v);
export function rejectionReason(call) {
  const result = object(call.result?.result);
  if (result.ok !== false) return "";
  const error = String(result.error || "");
  const rules = [
    [
      "Use patch_structure for local defects",
      "首次提取仅执行一次；局部问题使用补丁，整体错误使用受控重建工具",
    ],
    [
      "Initial extraction already exists",
      "已有结构化结果，必须局部修改，禁止整份重新生成",
    ],
    [
      "Extract with all current evidence and validate before finishing",
      "当前证据尚未完成应用与校验，不能直接结束",
    ],
    [
      "Keep the original question and field_paths",
      "更新缺口时改变了原问题或字段，应另建问题",
    ],
    [
      "latest Skill extraction still reports this gap",
      "结构化结果仍保留该缺口，暂不能宣称已解决",
    ],
    [
      "Inspect assembly capability and try targeted repairs before finish",
      "仍有拼接问题，需先检查能力并尝试针对性修复",
    ],
  ];
  return rules.find(([match]) => error.includes(match))?.[1] || "";
}
export function callStatus(call) {
  if (!call.result) return "running";
  const result = object(call.result.result);
  const data = payload(result);
  if (["phase_constraint", "pending_edits"].includes(result.repair?.code))
    return "blocked";
  if (rejectionReason(call)) return "blocked";
  if (result.ok === false || ["failed", "error"].includes(data.status))
    return "failed";
  if (
    ["needs_review", "unclear", "needs_revision"].includes(data.status) ||
    ["needs_revision", "uncertain", "review_error"].includes(
      data.structure_review?.status,
    ) ||
    data.backfill_errors?.length ||
    (Array.isArray(data.results) &&
      data.results.some((r) =>
        ["failed", "needs_review"].includes(object(r).status),
      ))
  )
    return "needs_review";
  return "completed";
}
export function callSummary(call) {
  if (!call.result) return "工具已开始，等待返回结果";
  const r = object(call.result.result),
    d = payload(r);
  if (rejectionReason(call)) return rejectionReason(call);
  if (r.repair?.code === "missing_attachment_values")
    return `所选连接点缺少完整基团值：${(r.repair.missing_keys || []).join("、")}。补充对应字段后重试。`;
  if (r.repair?.code === "pending_edits")
    return "仍有未应用的编辑，请先修正或提交；更新备注不能代替写入结果。";
  if (r.repair?.code === "phase_constraint")
    return "当前优先处理待修正编辑或收尾，暂停扩大检索范围。";
  if (r.ok === false) return String(r.error || d.error || "工具执行失败");
  if (d.dry_run) return "局部补丁预检通过，尚未应用，也未重新审核";
  if (call.name === "get_structure")
    return `读取版本 ${d.extraction_version} 的 ${d.path || "根目录"}${d.has_more ? "；还有内容可分页读取" : ""}`;
  if (typeof d.error === "string" && d.error) return d.error;
  if (d.structure_review)
    return `独立审查：${{ passed: "通过", needs_revision: "需要修改", uncertain: "待确认", review_error: "请求失败" }[d.structure_review.status] || d.structure_review.status || "已返回"}${d.patch ? "；已局部更新" : ""}`;
  if (d.backfill_errors?.length)
    return `校验发现 ${d.backfill_errors.length} 项拼接或回填问题`;
  if (Array.isArray(d.results))
    return `${d.results.length} 项结果，${d.results.filter((r) => r.status === "completed").length} 项完成`;
  if (Array.isArray(d.nodes))
    return `读取了 ${d.nodes.length} 个文本块，来源见下方预览`;
  if (Array.isArray(d.hits || d.matches))
    return `搜索命中 ${(d.hits || d.matches).length} 项，命中位置不代表已查看图像`;
  if (typeof d.text === "string")
    return `读取了 ${d.text.length} 字符的节点内容`;
  if (Array.isArray(d.candidates))
    return `找到 ${d.candidates.length} 个候选来源；候选定位不代表已查看图像`;
  if (Array.isArray(d.questions))
    return `整理了 ${d.questions.length} 个待查问题`;
  if (d.record) return "已登记补充信息，具体内容见下方返回结果";
  if (d.patch) return "已应用局部修改，具体字段见下方返回结果";
  if (d.evidence_id || r.evidence_id)
    return `已获取资料 · 证据 ${d.evidence_id || r.evidence_id}`;
  return String(
    d.summary ||
      d.note ||
      d.message ||
      (r.ok === undefined ? "已返回结果" : "调用完成"),
  );
}
export function callImages(call, evidence = [], base = "") {
  const result = object(call.result?.result);
  const matched = evidence.find((e) => e.id === result.evidence_id);
  const images = [];
  const visit = (value) => {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (Array.isArray(value.image_refs))
      for (const ref of value.image_refs) {
        const r = typeof ref === "string" ? { blob_ref: ref } : object(ref);
        if (
          typeof r.blob_ref !== "string" ||
          (typeof r.mime === "string" && !r.mime.startsWith("image/"))
        )
          continue;
        const page = Number.isInteger(r.display_page)
          ? r.display_page
          : Number.isInteger(r.page)
            ? r.page + 1
            : null;
        images.push({
          src: `/api/v1/blobs/${encodeURIComponent(r.blob_ref)}`,
          page,
          label: String(
            r.label || r.node_ref || value.label || value.node_ref || "",
          ),
          precision:
            r.precision ||
            (call.name === "preview_pages" ? "preview" : "detail"),
          viewed: true,
        });
      }
    if (value.data && typeof value.data === "object") visit(value.data);
  };
  if (call.result && result.ok !== false) {
    visit(result);
    if (matched) visit(matched.result);
  }
  // Page/node text reads get a UI source preview, clearly separated from images sent to the Agent.
  const args = object(call.result?.arguments || call.start?.arguments),
    data = payload(result);
  if (!images.length && call.result && result.ok !== false && base) {
    if (call.name === "get_page_text") {
      const page = data.page ?? args.page;
      if (Number.isInteger(page) && page >= 0)
        images.push({
          src: `${base}/pages/${page}/image?dpi=100`,
          page: page + 1,
          label: "文字来源页",
          precision: "source",
          viewed: false,
        });
    } else if (call.name === "get_node") {
      const ref =
        data.id ||
        data.node_id ||
        data.node_ref ||
        args.node_id ||
        args.node_ref;
      if (typeof ref === "string" && ref)
        images.push({
          src: `/api/v1/nodes/${ref.split("/").map(encodeURIComponent).join("/")}/crop?dpi=144&pad=0`,
          page: Number.isInteger(data.page) ? data.page + 1 : null,
          label: ref,
          precision: "source",
          viewed: false,
        });
    } else if (
      ["get_context_window", "get_section", "get_children"].includes(call.name)
    ) {
      const pages = [
        ...new Set(
          (Array.isArray(data.nodes || data.children)
            ? data.nodes || data.children
            : []
          )
            .map((n) => object(n).page)
            .filter((p) => Number.isInteger(p) && p >= 0),
        ),
      ];
      for (const page of pages)
        images.push({
          src: `${base}/pages/${page}/image?dpi=100`,
          page: page + 1,
          label: "文字来源页",
          precision: "source",
          viewed: false,
        });
    }
  }
  return images.filter(
    (x, i) =>
      images.findIndex((y) => y.src === x.src && y.page === x.page) === i,
  );
}
export function callText(call) {
  const data = payload(call.result?.result);
  const text = data.text || data.text_raw || data.text_norm || data.snippet;
  if (typeof text === "string") return [text];
  const rows = data.matches || data.hits || data.results || data.nodes || [];
  return Array.isArray(rows)
    ? rows
        .map(
          (r) =>
            object(r).snippet ||
            object(r).text ||
            object(r).text_raw ||
            object(r).caption ||
            object(r).preview,
        )
        .filter((t) => typeof t === "string")
    : [];
}

export function sourceLabels(events = [], documentMap = {}) {
  const labels = {};
  const visit = (value) => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    const v = object(value),
      id = v.node_id || v.node_ref || v.id;
    if (typeof id === "string" && typeof v.label === "string")
      labels[id] = v.label;
    // Navigation metadata only; extraction payloads do not establish source titles.
    for (const k of [
      "data",
      "result",
      "nodes",
      "items",
      "candidates",
      "questions",
      "children",
      "visuals",
    ])
      if (v[k]) visit(v[k]);
  };
  events.forEach(visit);
  visit(documentMap);
  return labels;
}

// Exact operation order, with one tool operation spanning its start/return.
// Independent audits executed inside that operation remain attached as child steps.
export function traceSteps(events = [], fallback = []) {
  const steps = [],
    pending = [];
  let activeAgent = null;
  const source = events.length
    ? events
    : fallback.map((data) => ({ kind: "agent_exploration", data }));
  let reviewInputs = [];
  for (let sequence = 0; sequence < source.length; sequence++) {
    const outer = source[sequence],
      e = { ...object(outer.data), at: outer.at };
    if (outer.kind === "model_request") {
      const refs = requestImageRefs(e.request);
      if (
        e.task === "figure_explore" &&
        activeAgent &&
        !activeAgent.entry.image_refs
      )
        activeAgent.entry.image_refs = refs;
      if (e.task === "figure_structure_review") reviewInputs = refs;
      if (e.task === "figure_structure") {
        const parent = pending.at(-1);
        if (parent) parent.call.inputImages = refs;
        else
          steps.push({
            type: "extraction",
            entry: e,
            image_refs: refs,
            sequence,
          });
      }
      continue;
    }
    if (outer.kind !== "agent_exploration") continue;
    if (e.kind === "context") {
      activeAgent = { type: "agent", entry: e, decision: null, sequence };
      steps.push(activeAgent);
    } else if (e.kind === "decision") {
      if (!activeAgent || activeAgent.decision) {
        activeAgent = { type: "agent", entry: {}, decision: null, sequence };
        steps.push(activeAgent);
      }
      activeAgent.decision = e;
    } else if (e.kind === "tool_start") {
      const step = {
        type: "tool",
        sequence,
        call: { name: e.name, callId: e.call_id, start: e, result: null },
        children: [],
      };
      steps.push(step);
      pending.push(step);
    } else if (e.kind === "tool") {
      const i = pending.findIndex((s) =>
        e.call_id
          ? s.call.callId === e.call_id
          : s.call.name === e.name && (!e.turn || s.call.start.turn === e.turn),
      );
      if (i >= 0) {
        pending[i].call.result = e;
        pending.splice(i, 1);
      } else
        steps.push({
          type: "tool",
          sequence,
          call: { name: e.name, start: null, result: e },
          children: [],
        });
    } else if (
      [
        "structure_review",
        "structure_review_cached",
        "reference_review",
      ].includes(e.kind)
    ) {
      const review = {
        type: "review",
        entry: e,
        image_refs: e.image_refs || reviewInputs,
        sequence,
      };
      reviewInputs = [];
      const parent = e.parent_call_id
        ? pending.find((s) => s.call.callId === e.parent_call_id)
        : pending.at(-1);
      if (parent) parent.children.push(review);
      else steps.push(review);
    } else steps.push({ type: e.kind, entry: e, sequence });
  }
  let previousInputs = null;
  for (const step of steps) {
    if (step.type !== "agent") continue;
    const refs = step.entry.image_refs;
    if (!Array.isArray(refs)) {
      previousInputs = null;
      continue;
    }
    step.inputDelta = {
      known: previousInputs !== null,
      added: refs.filter((r) => !previousInputs?.has(r.blob_ref)),
      total: refs.length,
    };
    previousInputs = new Set(refs.map((r) => r.blob_ref));
  }
  return steps;
}
export function requestImageRefs(request) {
  const images = [];
  for (const [index, message] of (object(request).messages || []).entries()) {
    if (!Array.isArray(message.content)) continue;
    let metadata = {};
    for (const part of message.content) {
      if (part.type === "text") {
        try {
          metadata = object(JSON.parse(part.text));
        } catch {
          metadata = {};
        }
      }
      if (part.type === "image" && typeof part.blob_ref === "string")
        images.push({
          blob_ref: part.blob_ref,
          mime: part.mime,
          page: metadata.page,
          display_page: metadata.display_page,
          evidence_id: metadata.evidence_id,
          precision: metadata.precision,
          role: index < 2 ? "target" : "reference",
        });
    }
  }
  return images;
}
