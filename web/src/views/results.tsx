import { useState } from "react";
import { Badge, Raw, ResourceState, useResource } from "../ui";
import { ApiError } from "../api";
import { arr, obj, text, type Navigate, type Obj } from "../contracts";
import { EvidenceLinks, Empty, Warnings } from "./shared";
import { downloadJSON } from "../debug.mjs";
function Field({
  label,
  value,
  audit,
  onEvidence,
  depth = 0,
}: {
  label: string;
  value: unknown;
  audit: unknown;
  onEvidence: Navigate;
  depth?: number;
}) {
  const a = obj(audit),
    isLeaf = Object.hasOwn(a, "value");
  if (value && typeof value === "object" && depth < 12 && !isLeaf) {
    const entries = Object.entries(value);
    return (
      <details className="result-field" open={depth < 2}>
        <summary>
          {label} <small>{entries.length} 项</small>
        </summary>
        {entries.slice(0, 200).map(([k, v]) => (
          <Field
            key={k}
            label={k}
            value={v}
            audit={obj(audit)[k]}
            onEvidence={onEvidence}
            depth={depth + 1}
          />
        ))}
        {entries.length > 200 && <p>仅预览前200项，完整结果请下载。</p>}
      </details>
    );
  }
  return (
    <div className="result-leaf">
      <div>
        <b>{label}</b>
        <p>{text(value, "未提供")}</p>
      </div>
      <div>
        <EvidenceLinks items={a.evidence} onEvidence={onEvidence} />
        {arr(a.source_ids).length > 0 && (
          <small className="source-ids">
            {arr(a.source_ids)
              .map((v) => text(v))
              .join(" · ")}
          </small>
        )}
      </div>
    </div>
  );
}
export function ResultsView({
  base,
  onEvidence,
  onRun,
}: {
  base: string;
  onEvidence: Navigate;
  onRun: (id: string) => void;
}) {
  const r = useResource<Obj>(`${base}/full-text`),
    [mode, setMode] = useState("fields");
  const projection = obj(r.data?.projection),
    delivery = obj(projection.delivery),
    audit = obj(projection.audit);
  const warning = [
    ...arr(projection.warnings),
    ...arr(obj(audit._audit).warnings),
  ];
  const empty = r.error instanceof ApiError && r.error.status === 404;
  return (
    <section className="card">
      <div className="section-head">
        <div>
          <h1>全文理解结果</h1>
          <p className="muted">
            当前 revision 与源指纹匹配的结果；字段缺失保持空值。
          </p>
        </div>
        <div className="toolbar">
          <button onClick={r.refresh}>刷新全文结果</button>
          {r.data && (
            <>
              <Badge status={text(r.data.status)} />
              <button
                onClick={() => downloadJSON("full-text-result.json", r.data)}
              >
                下载完整 JSON
              </button>
              {typeof r.data.run_id === "string" && (
                <button onClick={() => onRun(String(r.data?.run_id))}>
                  查看全文 Run
                </button>
              )}
            </>
          )}
        </div>
      </div>
      {empty ? (
        <Empty>
          当前论文尚无有效全文结果。可以先浏览文档、化合物和已有图像报告。
        </Empty>
      ) : (
        <ResourceState {...r} />
      )}
      <Warnings items={[...new Set(warning.map((v) => text(v)))]} />
      {r.data && (
        <>
          <div className="segmented">
            {[
              ["fields", "结构化字段"],
              ["audit", "审计与校验"],
              ["raw", "原始 JSON"],
            ].map(([id, label]) => (
              <button
                className={mode === id ? "active" : ""}
                key={id}
                onClick={() => setMode(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {mode === "fields" &&
            (Object.keys(delivery).length ? (
              Object.entries(delivery).map(([k, v]) => (
                <Field
                  key={k}
                  label={k}
                  value={v}
                  audit={audit[k]}
                  onEvidence={onEvidence}
                />
              ))
            ) : (
              <Empty>
                当前返回中没有 projection.delivery，检查原始响应中的 pending
                或失败原因。
              </Empty>
            ))}
          {mode === "audit" && (
            <>
              <h2>校验项</h2>
              <Raw value={r.data.checks} />
              <h2>路径与证据审计</h2>
              <Raw value={audit} />
            </>
          )}
          {mode === "raw" && <Raw value={r.data} />}
        </>
      )}
    </section>
  );
}
function Observations({
  label,
  values,
  onEvidence,
}: {
  label: string;
  values: unknown;
  onEvidence: Navigate;
}) {
  const rows = arr(values);
  if (!rows.length) return null;
  return (
    <div className="note-section">
      <h3>
        {label} · {rows.length}
      </h3>
      {rows.map((v, i) => {
        const value = obj(v);
        return (
          <article className="observation" key={i}>
            <p>
              {text(
                value.text ||
                  value.change ||
                  value.question ||
                  value.answer ||
                  value.label,
              )}
            </p>
            {typeof value.answer === "string" && (
              <p className="lookup-answer">{value.answer}</p>
            )}
            {value.motivation ? <p>动机：{text(value.motivation)}</p> : null}
            {value.outcome ? <p>结果：{text(value.outcome)}</p> : null}
            {value.from_label || value.to_label ? (
              <p>
                {text(value.from_label)} → {text(value.to_label)}
              </p>
            ) : null}
            {value.status ? <Badge status={text(value.status)} /> : null}
            <Warnings items={value.key_points} />
            <EvidenceLinks
              items={
                Array.isArray(value.evidence)
                  ? value.evidence
                  : arr(value.evidence_nodes).map((node_id) => ({ node_id }))
              }
              onEvidence={onEvidence}
            />
          </article>
        );
      })}
    </div>
  );
}
export function NotesView({
  base,
  interval,
  onEvidence,
}: {
  base: string;
  interval: number;
  onEvidence: Navigate;
}) {
  const [offset, setOffset] = useState(0),
    [selected, setSelected] = useState("");
  const r = useResource<{
    notes: {
      id: string;
      page: number;
      version: number;
      payload: Obj;
      fingerprint: string;
    }[];
    total: number;
    notice: string;
  }>(`${base}/reader-notes?limit=50&offset=${offset}`, interval);
  const current = r.data?.notes.find((v) => v.id === selected),
    payload = obj(current?.payload),
    notes = obj(payload.notes);
  // Reader persists {notes, deltas, page_mode, predecessor}; deltas retain the actual page changes.
  return (
    <>
      <div className="section-head">
        <div>
          <h1>阅读过程与回查</h1>
          <p className="muted">
            {r.data?.notice || "当前 revision 的 active 阅读检查点"}
          </p>
        </div>
        <button onClick={r.refresh}>刷新阅读检查点</button>
      </div>
      <ResourceState {...r} />
      <div className="split">
        <section className="card">
          {r.data?.notes.map((n) => (
            <button
              className={`report-row ${selected === n.id ? "selected" : ""}`}
              key={n.id}
              onClick={() => setSelected(n.id)}
            >
              <b>第 {n.page + 1} 页</b>
              <small>
                版本 {n.version} · {n.id}
              </small>
            </button>
          ))}
          {!r.loading && !r.error && !r.data?.notes.length && (
            <Empty>
              尚无已保存的阅读检查点。缺失材料或 pending 会保留在运行记录中。
            </Empty>
          )}
          <div className="toolbar">
            <button
              disabled={!offset}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              上一页
            </button>
            <span>
              {offset} / {r.data?.total ?? 0}
            </span>
            <button
              disabled={!r.data || offset + 50 >= r.data.total}
              onClick={() => setOffset(offset + 50)}
            >
              下一页
            </button>
          </div>
        </section>
        <section className="card">
          {current ? (
            <>
              <h2>第 {current.page + 1} 页 · 阅读检查点</h2>
              <code>fingerprint: {current.fingerprint}</code>
              <div className="stats compact">
                {[
                  ["累计已读页", arr(notes.pages_read).length],
                  ["累计模型调用", notes.model_calls ?? "—"],
                  ["累计工具调用", notes.tool_calls ?? "—"],
                ].map(([k, v]) => (
                  <div className="card" key={text(k)}>
                    <span>{text(k)}</span>
                    <strong>{text(v)}</strong>
                  </div>
                ))}
              </div>
              <Warnings items={notes.warnings} />
              {arr(payload.deltas).map((delta, i) => {
                const d = obj(delta);
                return (
                  <article key={i}>
                    <h2>本页增量 {i + 1}</h2>
                    {[
                      ["thesis_updates", "主题更新"],
                      ["compound_updates", "化合物更新"],
                      ["narrative_events", "优化叙事"],
                      ["sar_observations", "SAR 观察"],
                      ["open_questions", "新增疑问"],
                    ].map(([key, label]) => (
                      <Observations
                        key={key}
                        label={label}
                        values={d[key]}
                        onEvidence={onEvidence}
                      />
                    ))}
                  </article>
                );
              })}
              <Observations
                label="待查问题（累计）"
                values={notes.open_questions}
                onEvidence={onEvidence}
              />
              <Observations
                label="回查答案（累计）"
                values={notes.lookup_answers}
                onEvidence={onEvidence}
              />
              <details>
                <summary>完整检查点 JSON</summary>
                <Raw value={payload} />
              </details>
            </>
          ) : (
            <Empty>选择页检查点，检查逐页增量、未解决问题和回查证据。</Empty>
          )}
        </section>
      </div>
    </>
  );
}
