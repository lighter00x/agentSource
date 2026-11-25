import { useState } from "react";
import { Badge, Raw, ResourceState, useResource } from "../ui";
import {
  arr,
  obj,
  text,
  type Compound,
  type Measurement,
  type Navigate,
  type Obj,
} from "../contracts";
import { EvidenceLinks, Empty, Warnings } from "./shared";
import { Molecule } from "./molecule";
import { downloadJSON } from "../debug.mjs";
export function CompoundsView({
  base,
  onEvidence,
}: {
  base: string;
  onEvidence: Navigate;
}) {
  const [offset, setOffset] = useState(0),
    [query, setQuery] = useState(""),
    [selected, setSelected] = useState("");
  const r = useResource<Compound[]>(
    `${base}/compounds?limit=50&offset=${offset}`,
  );
  const detail = useResource<{
    compound: Compound;
    structures: Obj[];
    conflicts: Obj[];
  }>(selected ? `${base}/compounds/${encodeURIComponent(selected)}` : null);
  const rows = r.data || [],
    filtered = rows.filter((c) =>
      `${c.primary_label} ${arr(c.aliases).join(" ")} ${c.role}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    );
  const structure = detail.data?.structures.find(
    (s) => s.structure_id === detail.data?.compound.accepted_structure_id,
  );
  return (
    <>
      <div className="section-head">
        <div>
          <h1>化合物台账</h1>
          <p className="muted">
            已保存的化合物、结构来源与冲突。结构图在本地绘制。
          </p>
        </div>
        <button
          onClick={() => {
            r.refresh();
            detail.refresh();
          }}
        >
          刷新台账
        </button>
      </div>
      <div className="split">
        <section className="card">
          <label>
            本页筛选
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="编号 / 别名 / 角色"
            />
          </label>
          <ResourceState {...r} />
          <div className="compound-list">
            {filtered.map((c) => (
              <button
                key={c.compound_id}
                className={`compound-row ${selected === c.compound_id ? "selected" : ""}`}
                onClick={() => setSelected(c.compound_id)}
              >
                <b>{c.primary_label}</b>
                <span>{arr(c.aliases).join(" · ") || "无别名"}</span>
                <Badge status={c.role_status || "unknown"} />
                <small>
                  {c.role || "unknown"} · {c.status || "active"}
                </small>
              </button>
            ))}
          </div>
          {!r.loading && !r.error && !filtered.length && (
            <Empty>没有匹配的已保存化合物。</Empty>
          )}
          <div className="toolbar">
            <button
              disabled={!offset}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              上一页
            </button>
            <span>第 {Math.floor(offset / 50) + 1} 页</span>
            <button
              disabled={rows.length < 50}
              onClick={() => setOffset(offset + 50)}
            >
              下一页
            </button>
          </div>
        </section>
        <section className="card">
          <h2>结构与证据</h2>
          <ResourceState {...detail} />
          {detail.data ? (
            <>
              <h2>化合物 {detail.data.compound.primary_label}</h2>
              <EvidenceLinks
                items={[
                  ...arr(detail.data.compound.evidence),
                  ...arr(detail.data.compound.role_evidence),
                ]}
                onEvidence={onEvidence}
              />
              {structure ? (
                <>
                  <Molecule
                    smiles={text(
                      structure.smiles_core ||
                        structure.canonical_smiles ||
                        structure.e_smiles_raw,
                      "",
                    )}
                  />
                  <p>
                    已采纳来源：<b>{text(structure.source)}</b>
                  </p>
                  <code>
                    {text(structure.e_smiles_raw || structure.canonical_smiles)}
                  </code>
                  <EvidenceLinks
                    items={structure.evidence}
                    onEvidence={onEvidence}
                  />
                </>
              ) : (
                <Empty>
                  尚无已采纳结构。候选与失败信息保留在下方，不自动选择候选。
                </Empty>
              )}
              {detail.data.conflicts.length > 0 && (
                <div className="error">
                  <b>{detail.data.conflicts.length} 项待处理冲突</b>
                  <Raw value={detail.data.conflicts} />
                </div>
              )}
              <details>
                <summary>
                  全部结构候选 · {detail.data.structures.length}
                </summary>
                {detail.data.structures.map((s) => (
                  <div className="candidate" key={text(s.structure_id)}>
                    <Badge status={text(s.source)} />
                    <code>{text(s.structure_id)}</code>
                    <Raw value={s} />
                  </div>
                ))}
              </details>
              <details>
                <summary>台账原始数据</summary>
                <Raw value={detail.data} />
              </details>
              <button
                onClick={() =>
                  downloadJSON(`compound-${selected}.json`, detail.data)
                }
              >
                导出台账 JSON
              </button>
            </>
          ) : (
            !selected && <Empty>选择左侧化合物，检查结构、来源和证据。</Empty>
          )}
        </section>
      </div>
    </>
  );
}
export function MeasurementsView({
  base,
  onEvidence,
}: {
  base: string;
  onEvidence: Navigate;
}) {
  const r = useResource<Measurement[]>(`${base}/measurements`),
    assays = useResource<Obj[]>(`${base}/assays`);
  const [q, setQ] = useState(""),
    [status, setStatus] = useState(""),
    [selected, setSelected] = useState("");
  const rows = (r.data || []).filter(
    (m) =>
      (!status || m.status === status) &&
      `${m.subject_label} ${m.compound_id} ${m.metric} ${m.value_raw}`
        .toLowerCase()
        .includes(q.toLowerCase()),
  );
  const item = r.data?.find((m) => m.measurement_id === selected),
    assay = assays.data?.find((a) => a.assay_id === item?.assay_id);
  return (
    <section className="card">
      <div className="section-head">
        <div>
          <h1>测定与实验条件</h1>
          <p className="muted">
            保持原值、单位与物种条件；待复核值不合并为可靠结果。
          </p>
        </div>
        <button
          onClick={() => {
            r.refresh();
            assays.refresh();
          }}
        >
          刷新测定
        </button>
      </div>
      <div className="toolbar">
        <label>
          筛选测定
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="化合物 / 指标 / 数值"
          />
        </label>
        <label>
          测定状态
          <select
            aria-label="测定状态"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">全部</option>
            {[
              "explicit",
              "estimated",
              "not_determined",
              "inactive",
              "derived",
              "needs_review",
            ].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <span>{rows.length} 条</span>
        <button
          disabled={!r.data}
          onClick={() => downloadJSON("measurements.json", r.data)}
        >
          导出 JSON
        </button>
      </div>
      <ResourceState {...r} />
      <ResourceState {...assays} />
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {[
                "化合物",
                "指标",
                "原始值",
                "单位",
                "状态",
                "来源",
                "证据",
                "详情",
              ].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.measurement_id}>
                <td>{m.subject_label || m.compound_id || "未关联"}</td>
                <td>{m.metric_label_raw || m.metric}</td>
                <td>{m.value_raw}</td>
                <td>{m.unit_raw || m.unit || "—"}</td>
                <td>
                  <Badge status={m.status} />
                </td>
                <td>{m.source_kind}</td>
                <td>
                  <EvidenceLinks items={m.evidence} onEvidence={onEvidence} />
                </td>
                <td>
                  <button onClick={() => setSelected(m.measurement_id)}>
                    查看条件
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!r.loading && !r.error && !rows.length && (
        <Empty>没有匹配的已提交测定数据。</Empty>
      )}
      {item && (
        <div className="detail-block">
          <h2>测定详情 · {item.metric}</h2>
          <Warnings
            items={
              item.normalization_warning ? [item.normalization_warning] : []
            }
          />
          <h3>实验条件</h3>
          {assay ? (
            <>
              <dl className="key-values">
                {["name_raw", "target", "species", "system", "assay_type"].map(
                  (k) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{text(assay[k])}</dd>
                    </div>
                  ),
                )}
              </dl>
              <Raw value={obj(assay.conditions)} />
              <EvidenceLinks items={assay.evidence} onEvidence={onEvidence} />
            </>
          ) : (
            <p>未关联已保存的实验条件。</p>
          )}
          <details>
            <summary>测定原始 JSON</summary>
            <Raw value={item} />
          </details>
        </div>
      )}
    </section>
  );
}
