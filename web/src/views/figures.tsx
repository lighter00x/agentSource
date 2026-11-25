import { useState } from "react";
import { figureInventory } from "../figure-inventory.mjs";
import type { Tree } from "../api";
import { Badge, Raw, ResourceState, useResource } from "../ui";
import {
  arr,
  obj,
  text,
  normalizedBox,
  type Box,
  type Obj,
  type FigureReport,
  type Navigate,
} from "../contracts";
import { Crop, EvidenceLinks, Empty, Warnings } from "./shared";
import { downloadJSON } from "../debug.mjs";
import { FigureResultFile } from "./figure-result";
function TablePreview({
  data,
  nodeRef,
  onEvidence,
  onCell,
}: {
  data: Obj;
  nodeRef: string;
  onEvidence: Navigate;
  onCell: (b: Box | null) => void;
}) {
  const [selected, setSelected] = useState<{
    row: number;
    col: number;
    value: Obj;
  } | null>(null);
  const headers = arr(data.header_rows),
    rows = arr(data.rows);
  function cells(row: unknown, ri: number, header = false) {
    return arr(row).map((item, ci) => {
      const c = obj(item),
        Tag = header ? "th" : "td";
      return (
        <Tag
          key={ci}
          rowSpan={Math.max(1, Math.min(Number(c.row_span) || 1, 100))}
          colSpan={Math.max(1, Math.min(Number(c.col_span) || 1, 100))}
        >
          <button
            className="cell-button"
            onClick={() => {
              setSelected({ row: ri, col: ci, value: c });
              onCell(normalizedBox(c.bbox));
            }}
          >
            {text(
              c.text,
              c.mol_id !== null && c.mol_id !== undefined
                ? `分子 ${c.mol_id}`
                : "—",
            )}
            {c.status && c.status !== "explicit" ? (
              <Badge status={text(c.status)} />
            ) : null}
          </button>
        </Tag>
      );
    });
  }
  return (
    <>
      <h3>{text(data.title, "抽取表格")}</h3>
      <div className="table-scroll">
        <table>
          <thead>
            {headers.map((row, i) => (
              <tr key={i}>{cells(row, i, true)}</tr>
            ))}
          </thead>
          <tbody>
            {rows.slice(0, 200).map((row, i) => (
              <tr key={i}>{cells(row, i)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 200 && <p>预览前200行，完整内容可导出 JSON。</p>}
      <Warnings
        items={Object.entries(obj(data.footnote_markers)).map(
          ([k, v]) => `${k}: ${text(v)}`,
        )}
      />
      <Warnings items={data.notes} />
      {selected && (
        <div className="detail-block">
          <h3>
            单元格 · 行 {selected.row + 1} / 列 {selected.col + 1}
          </h3>
          <Raw value={selected.value} />
          <EvidenceLinks
            items={[{ node_id: nodeRef, cell: [selected.row, selected.col] }]}
            onEvidence={onEvidence}
          />
          <small>单元格坐标保留供核查，论文页高亮定位到来源节点。</small>
        </div>
      )}
    </>
  );
}
const colors = ["#168b8b", "#d49029", "#7f6ec6", "#c45c72", "#527fbb"];
function ChartPreview({ data }: { data: Obj }) {
  const series = arr(data.series).map((v, i) => ({
    label: text(obj(v).label, `系列 ${i + 1}`),
    points: arr(obj(v).points).filter(
      (p) =>
        Array.isArray(p) &&
        p.length >= 2 &&
        Number.isFinite(p[0]) &&
        Number.isFinite(p[1]),
    ) as number[][],
  }));
  const x = obj(data.x_axis),
    y = obj(data.y_axis),
    valid = series
      .flatMap((s) => s.points)
      .filter(
        (p) =>
          (x.scale !== "log10" || p[0] > 0) &&
          (y.scale !== "log10" || p[1] > 0),
      );
  const transform = (n: number, axis: Obj) =>
    axis.scale === "log10" ? Math.log10(n) : n;
  const xs = valid.map((p) => transform(p[0], x)),
    ys = valid.map((p) => transform(p[1], y));
  const xmin = Math.min(...xs),
    xmax = Math.max(...xs),
    ymin =
      data.chart_type === "bar" && y.scale !== "log10"
        ? Math.min(0, ...ys)
        : Math.min(...ys),
    ymax =
      data.chart_type === "bar" && y.scale !== "log10"
        ? Math.max(0, ...ys)
        : Math.max(...ys);
  const px = (n: number) =>
      60 + ((transform(n, x) - xmin) / (xmax - xmin || 1)) * 450,
    py = (n: number) =>
      230 - ((transform(n, y) - ymin) / (ymax - ymin || 1)) * 190;
  return (
    <>
      <h3>数据曲线 · {text(data.chart_type)}</h3>
      <p className="muted">
        仅绘制已保存数值点；log10 坐标跳过非正值，原始点保留在数据表。
      </p>
      {valid.length ? (
        <svg
          className="chart-preview"
          role="img"
          aria-label="已保存图表数据预览"
          viewBox="0 0 560 290"
        >
          <path d="M60 30V230H520" fill="none" stroke="#9aaaba" />
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line
                x1="60"
                y1={230 - f * 190}
                x2="520"
                y2={230 - f * 190}
                stroke="#e5ebf0"
              />
              <text x="54" y={234 - f * 190} textAnchor="end" fontSize="10">
                {(y.scale === "log10"
                  ? 10 ** (ymin + f * (ymax - ymin))
                  : ymin + f * (ymax - ymin)
                ).toPrecision(3)}
              </text>
              <text x={60 + f * 450} y="247" textAnchor="middle" fontSize="10">
                {(x.scale === "log10"
                  ? 10 ** (xmin + f * (xmax - xmin))
                  : xmin + f * (xmax - xmin)
                ).toPrecision(3)}
              </text>
            </g>
          ))}
          {series.map((s, i) => {
            const points = s.points.filter(
              (p) =>
                (x.scale !== "log10" || p[0] > 0) &&
                (y.scale !== "log10" || p[1] > 0),
            );
            return (
              <g
                key={i}
                stroke={colors[i % colors.length]}
                fill={colors[i % colors.length]}
              >
                {data.chart_type === "line" && (
                  <polyline
                    fill="none"
                    strokeWidth="2"
                    points={points
                      .map((p) => `${px(p[0])},${py(p[1])}`)
                      .join(" ")}
                  />
                )}
                {points.map((p, j) =>
                  data.chart_type === "bar" ? (
                    <rect
                      key={j}
                      x={px(p[0]) - 5 + i * 3}
                      y={Math.min(py(p[1]), y.scale === "log10" ? 230 : py(0))}
                      width="10"
                      height={Math.abs(
                        py(p[1]) - (y.scale === "log10" ? 230 : py(0)),
                      )}
                    >
                      <title>{`${s.label}: ${p.join(", ")}`}</title>
                    </rect>
                  ) : (
                    <circle key={j} cx={px(p[0])} cy={py(p[1])} r="3">
                      <title>{`${s.label}: ${p.join(", ")}`}</title>
                    </circle>
                  ),
                )}
              </g>
            );
          })}
          <text x="285" y="278" textAnchor="middle" fontSize="12">
            {text(x.title, "x")} {text(x.unit, "")} · {text(x.scale, "linear")}
          </text>
          <text
            transform="translate(14 135) rotate(-90)"
            textAnchor="middle"
            fontSize="12"
          >
            {text(y.title, "y")} {text(y.unit, "")}
          </text>
        </svg>
      ) : (
        <Empty>暂无可绘制的数值点。仅像素坐标不转换为测定值。</Empty>
      )}
      <div className="chart-legend">
        {series.map((s, i) => (
          <span key={i} style={{ color: colors[i % colors.length] }}>
            ● {s.label}
          </span>
        ))}
      </div>
      <details>
        <summary>点数据与坐标标定</summary>
        {series.map((s, i) => (
          <div key={i}>
            <b>{s.label}</b>
            <Raw value={s.points} />
          </div>
        ))}
        <Raw value={{ x_axis: x, y_axis: y, calibration: data.calibration }} />
      </details>
    </>
  );
}
export function FiguresView({
  base,
  onEvidence,
  onRun,
}: {
  base: string;
  onEvidence: Navigate;
  onRun: (id: string) => void;
}) {
  const r = useResource<FigureReport[]>(`${base}/figure-reports`);
  const tree = useResource<{ root: Tree }>(`${base}/tree`);
  const [sourceRef, setSourceRef] = useState("");
  const [selected, setSelected] = useState(""),
    [status, setStatus] = useState(""),
    [kind, setKind] = useState(""),
    [version, setVersion] = useState("extraction");
  const [cell, setCell] = useState<Box | null>(null);
  const reports = r.data || [],
    current = reports.find((v) => v.report_id === selected),
    data = obj(obj(current)[version]);
  const filtered = reports.filter(
    (v) => (!status || v.status === status) && (!kind || v.image_type === kind),
  );
  const inventory = figureInventory(tree.data?.root, reports);
  const pending = inventory.filter((n) => !n.report);
  const known = tree.data !== undefined && r.data !== undefined;
  return (
    <>
      <div className="section-head">
        <div>
          <h1>图像理解与图表</h1>
          <p className="muted">
            已保存报告包含历史版本；按报告 ID 核对状态与来源。
          </p>
        </div>
        <button
          onClick={() => {
            r.refresh();
            tree.refresh();
          }}
        >
          刷新图像报告
        </button>
      </div>
      <ResourceState {...r} />
      <ResourceState {...tree} />
      <div className="stats">
        {[
          ["解析发现图表", tree.data ? inventory.length : "—"],
          ["已有提取报告", known ? inventory.length - pending.length : "—"],
          ["尚无提取报告", known ? pending.length : "—"],
          [
            "最新报告失败",
            known
              ? inventory.filter((n) => n.report?.status === "failed").length
              : "—",
          ],
        ].map(([k, v]) => (
          <div className="card" key={String(k)}>
            <span>{k}</span>
            <strong>{v}</strong>
          </div>
        ))}
      </div>
      {known && pending.length > 0 && (
        <p className="warning-banner">
          解析发现 {inventory.length} 个图表节点，其中 {pending.length}{" "}
          个尚无提取报告。文档 ready
          仅表示解析完成，不代表表格单元格、曲线数据或分子信息已提取。
        </p>
      )}
      <p className="muted">
        报告状态由提取器记录；有报告或状态为 ok 均不代表经过独立内容准确性验收。
      </p>
      <div className="split">
        <section className="card">
          <div className="toolbar">
            <label>
              报告状态
              <select
                aria-label="报告状态"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="">全部</option>
                {["ok", "remediated", "needs_review", "failed"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label>
              图像类型
              <select
                aria-label="图像类型"
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              >
                <option value="">全部</option>
                {[...new Set(reports.map((v) => v.image_type))].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
          </div>
          {known && pending.length > 0 && (
            <section aria-label="尚无提取报告的图表">
              <h2>已发现，尚无提取报告</h2>
              {pending.map((n) => (
                <button
                  className={`report-row ${sourceRef === n.id ? "selected" : ""}`}
                  key={n.id}
                  onClick={() => {
                    setSourceRef(n.id);
                    setSelected("");
                    setCell(null);
                  }}
                >
                  <div>
                    <b>{n.label || n.type}</b>
                    <Badge status="pending" />
                  </div>
                  <code>{n.id}</code>
                  <small>
                    {typeof n.page === "number" ? `第 ${n.page + 1} 页 · ` : ""}
                    仅有源图，可查看原图核对
                  </small>
                </button>
              ))}
            </section>
          )}
          <h2>已保存的报告</h2>
          {filtered.map((v) => (
            <button
              className={`report-row ${selected === v.report_id ? "selected" : ""}`}
              key={v.report_id}
              onClick={() => {
                setSelected(v.report_id);
                setSourceRef("");
                setCell(null);
              }}
            >
              <div>
                <b>{v.image_type}</b>
                <Badge status={v.status} />
              </div>
              <code>{v.node_ref}</code>
              <small>
                {v.report_id} · {arr(v.checks).length} 项检查 ·{" "}
                {arr(v.open_questions).length} 个疑问
              </small>
            </button>
          ))}
          {!r.loading && !r.error && !filtered.length && (
            <Empty>
              暂无匹配图像报告。无需启动外部服务即可浏览其他已有数据。
            </Empty>
          )}
        </section>
        <section className="card">
          {sourceRef ? (
            <>
              <h2>源图 · 尚无提取报告</h2>
              <p>
                当前可查看解析得到的原图，但尚无已提交的结构化提取结果。此预览不生成表格数值、曲线点或分子结构。
              </p>
              <EvidenceLinks
                items={[{ node_id: sourceRef }]}
                onEvidence={onEvidence}
              />
              <Crop nodeRef={sourceRef} />
            </>
          ) : current ? (
            <>
              <div className="section-head">
                <h2>{current.image_type} · 图像报告</h2>
                <Badge status={current.status} />
              </div>
              <div className="toolbar">
                <EvidenceLinks
                  items={[{ node_id: current.node_ref }]}
                  onEvidence={onEvidence}
                />
                {current.run_id && (
                  <button onClick={() => onRun(current.run_id!)}>
                    查看图像 Run
                  </button>
                )}
                <button
                  onClick={() =>
                    downloadJSON(`${current.report_id}.json`, current)
                  }
                >
                  导出报告
                </button>
              </div>
              {Object.keys(obj(current.backfilled_extraction)).length > 0 && (
                <FigureResultFile
                  endpoint={`${base}/figure-reports/${encodeURIComponent(current.report_id)}/result`}
                />
              )}
              <Crop nodeRef={current.node_ref} highlight={cell} />
              <label>
                产物版本
                <select
                  aria-label="产物版本"
                  value={version}
                  onChange={(e) => {
                    setVersion(e.target.value);
                    setCell(null);
                  }}
                >
                  <option value="extraction">当前抽取</option>
                  <option value="pre_backfill_extraction">结构回填前</option>
                  <option value="backfilled_extraction">结构回填后</option>
                </select>
              </label>
              {Array.isArray(data.rows) ? (
                <TablePreview
                  key={`${current.report_id}:${version}`}
                  data={data}
                  onCell={setCell}
                  nodeRef={current.node_ref}
                  onEvidence={onEvidence}
                />
              ) : Array.isArray(data.series) ? (
                <ChartPreview data={data} />
              ) : (
                <details open>
                  <summary>结构化内容</summary>
                  <Raw value={data} />
                </details>
              )}
              <Warnings items={current.open_questions} />
              <details open={!!current.checks?.length}>
                <summary>检查与复核 · {arr(current.checks).length}</summary>
                {arr(current.checks).map((c, i) => (
                  <div className="check-item" key={i}>
                    <Badge status={text(obj(c).severity)} />
                    <b>{text(obj(c).code)}</b>
                    <p>{text(obj(c).message)}</p>
                    <code>{text(obj(c).field_path)}</code>
                  </div>
                ))}
                <Raw value={current.review} />
                <Raw value={current.unchecked} />
              </details>
              <details>
                <summary>Skill 执行过程与调用统计</summary>
                <Raw value={current.accounting} />
                {arr(current.trace).map((v, i) => (
                  <div className="trace-step" key={i}>
                    <Badge status={text(obj(v).status)} />
                    <b>{text(obj(v).step)}</b>
                    {typeof obj(v).run_id === "string" && (
                      <button onClick={() => onRun(String(obj(v).run_id))}>
                        查看步骤 Run
                      </button>
                    )}
                    <Raw value={v} />
                  </div>
                ))}
              </details>
              <details>
                <summary>完整报告与子报告</summary>
                <Raw value={current} />
              </details>
            </>
          ) : (
            <Empty>选择图像报告，查看原图、表格、曲线、自检与执行过程。</Empty>
          )}
        </section>
      </div>
    </>
  );
}
