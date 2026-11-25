import { useState } from "react";
import { Badge, Raw, ResourceState, useResource } from "../ui";
import { arr, text, type Navigate, type Obj } from "../contracts";
import { Empty, EvidenceLinks } from "./shared";
export function SearchView({
  base,
  onEvidence,
}: {
  base: string;
  onEvidence: Navigate;
}) {
  const [q, setQ] = useState(""),
    [mode, setMode] = useState("hybrid"),
    [literal, setLiteral] = useState(true),
    [path, setPath] = useState<string | null>(null);
  const r = useResource<{ results: Obj[]; truncated: boolean }>(path);
  return (
    <section className="card">
      <h1>论文内检索</h1>
      <p className="muted">
        调用现有检索接口，不调用模型。正则模式的错误会直接显示。
      </p>
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          const p = new URLSearchParams({
            q,
            mode,
            literal: String(literal),
            limit: "50",
          });
          setPath(`${base}/search?${p}`);
          if (path === `${base}/search?${p}`) r.refresh();
        }}
      >
        <label>
          检索内容
          <input
            required
            minLength={1}
            maxLength={500}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="如 UCM-1306 / hERG"
          />
        </label>
        <label>
          检索模式
          <select
            aria-label="检索模式"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            {["hybrid", "fts", "trgm", "grep"].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        {mode === "grep" && (
          <label>
            <input
              type="checkbox"
              checked={literal}
              onChange={(e) => setLiteral(e.target.checked)}
            />
            字面匹配（关闭使用正则）
          </label>
        )}
        <button className="primary" disabled={!q.trim()}>
          检索
        </button>
      </form>
      <ResourceState {...r} />
      {r.data?.truncated && (
        <p className="warning-banner">仅返回前50条，请缩小检索范围。</p>
      )}
      {r.data?.results.map((v, i) => (
        <article className="search-result" key={i}>
          <div className="section-head">
            <code>{text(v.node_id)}</code>
            <Badge status={text(v.type)} />
          </div>
          <p>{text(v.snippet)}</p>
          <EvidenceLinks items={[v]} onEvidence={onEvidence} />
          <small>
            {v.score !== null && v.score !== undefined
              ? `相关度 ${text(v.score)}`
              : ""}
          </small>
        </article>
      ))}
      {r.data && !r.data.results.length && <Empty>未找到匹配原文。</Empty>}
    </section>
  );
}
export function CapabilitiesView() {
  const [q, setQ] = useState(""),
    [selected, setSelected] = useState("");
  const r = useResource<Obj[]>("/api/v1/skills/catalog?audience=chat");
  const versions = useResource<Obj[]>(
    selected ? `/api/v1/skills/${encodeURIComponent(selected)}/versions` : null,
  );
  return (
    <>
      <div className="section-head">
        <div>
          <h1>能力与版本检查</h1>
          <p className="muted">
            后端当前可见的能力目录。此页只读，不执行 Skill 或热加载。
          </p>
        </div>
        <button onClick={r.refresh}>刷新能力目录</button>
      </div>
      <ResourceState {...r} />
      <label>
        筛选能力
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="名称 / 描述"
        />
      </label>
      <div className="capability-grid">
        {r.data
          ?.filter((v) =>
            JSON.stringify(v).toLowerCase().includes(q.toLowerCase()),
          )
          .map((v, i) => (
            <article className="card" key={text(v.name, String(i))}>
              <h2>{text(v.name)}</h2>
              <p>{text(v.l0 || v.description || v.summary)}</p>
              <p className="muted">{text(v.l1, "")}</p>
              <button onClick={() => setSelected(text(v.name, ""))}>
                检查注册版本
              </button>
              <details>
                <summary>能力卡片</summary>
                <Raw value={v} />
              </details>
            </article>
          ))}
      </div>
      {selected && (
        <section className="card">
          <h2>{selected} · 注册版本</h2>
          <ResourceState {...versions} />
          {versions.data?.length === 0 && (
            <Empty>
              未找到注册的 Skill 版本；工具能力可能没有独立 Skill 版本记录。
            </Empty>
          )}
          {versions.data?.map((v, i) => (
            <div className="check-item" key={i}>
              <Badge status={text(v.status)} />
              <b>{text(v.version)}</b>
              <Raw value={v} />
            </div>
          ))}
        </section>
      )}
    </>
  );
}
export function BatchView({ interval }: { interval: number }) {
  const [input, setInput] = useState(""),
    [id, setId] = useState("");
  const r = useResource<Obj>(
    id ? `/api/v1/batches/${encodeURIComponent(id)}` : null,
    interval,
  );
  return (
    <section className="card">
      <h1>批次状态</h1>
      <p className="muted">
        输入已有批次 ID
        查看任务及文档结果。接口未提供批次列表，因此不显示推测的历史批次。
      </p>
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          setId(input.trim());
          if (id === input.trim()) r.refresh();
        }}
      >
        <label>
          批次 ID
          <input
            required
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="batch_…"
          />
        </label>
        <button disabled={!input.trim()}>查看批次</button>
      </form>
      <ResourceState {...r} />
      {r.data && (
        <>
          <Badge status={text(r.data.status)} />
          <p>
            任务数 {arr(r.data.task_ids).length} · 文档数{" "}
            {arr(r.data.document_ids).length}
          </p>
          <Raw value={r.data} />
        </>
      )}
    </section>
  );
}
