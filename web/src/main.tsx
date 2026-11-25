import React, {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { createRoot } from "react-dom/client";
import {
  request,
  logStore,
  type Doc,
  type Run,
  type Runs,
  type Tree,
  type Node,
} from "./api";
import { downloadJSON, timeline } from "./debug.mjs";
import "./style.css";
import { CompoundsView, MeasurementsView } from "./views/compounds";
import { FiguresView } from "./views/figures";
import { WorkbenchView } from "./views/workbench";
import { OcrContent } from "./views/ocr";
import { ResultsView, NotesView } from "./views/results";
import { SearchView, CapabilitiesView, BatchView } from "./views/tools";
import { obj, refPage, evidenceTextParts, type Evidence } from "./contracts";
import { pretty, Raw, Badge, useResource, ResourceState } from "./ui";
const enc = encodeURIComponent;
function RunMonitor({
  base,
  interval,
  focusRun,
}: {
  base: string;
  interval: number;
  focusRun?: string;
}) {
  const [status, setStatus] = useState(""),
    [component, setComponent] = useState(""),
    [trace, setTrace] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState("");
  useEffect(() => {
    if (focusRun) setSelected(focusRun);
  }, [focusRun]);
  const params = new URLSearchParams({ limit: "50", offset: String(offset) });
  if (status) params.set("status", status);
  if (component) params.set("component", component);
  if (trace) params.set("trace_id", trace);
  const r = useResource<Runs>(`${base}/runs?${params}`, interval),
    detail = useResource<Record<string, unknown>>(
      selected ? `${base}/runs/${enc(selected)}` : null,
      interval,
    );
  const rows = r.data?.runs || [],
    bars = timeline(rows);
  const [search, setSearch] = useState("");
  const visible = rows.filter((x) =>
    `${x.id} ${x.component} ${pretty(x.error)}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="section-head">
        <div>
          <h1>Agent 运行监控</h1>
          <p className="muted">
            持久化记录 · 含历史 revision · 不推测缺失的父子关系
          </p>
        </div>
        <button
          onClick={() => {
            r.refresh();
            detail.refresh();
          }}
        >
          立即刷新
        </button>
      </div>
      <div className="stats">
        {[
          ["匹配记录", r.data?.total ?? "—"],
          ["本页运行中", rows.filter((x) => x.status === "running").length],
          [
            "本页失败",
            rows.filter((x) => ["failed", "error"].includes(x.status)).length,
          ],
          ["最近成功读取", r.at || "—"],
        ].map(([k, v]) => (
          <div className="card" key={String(k)}>
            <span>{k}</span>
            <strong>{v}</strong>
          </div>
        ))}
      </div>
      <div className="card filters">
        <label>
          状态
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部</option>
            {[
              "running",
              "completed",
              "succeeded",
              "failed",
              "pending",
              "needs_review",
              "cached",
            ].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          组件（精确匹配）
          <input
            value={component}
            onChange={(e) => {
              setComponent(e.target.value);
              setOffset(0);
            }}
            placeholder="如 reader_read"
          />
        </label>
        <label>
          Trace ID
          <input
            value={trace}
            onChange={(e) => {
              setTrace(e.target.value);
              setOffset(0);
            }}
            placeholder="服务端筛选"
          />
        </label>
        <label>
          本页搜索
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Run ID / 组件 / 错误"
          />
        </label>
      </div>
      <ResourceState {...r} />
      {r.error && r.data && (
        <p className="error">以下保留上次成功快照，当前连接异常。</p>
      )}
      <div className="split">
        <section className="card">
          <h2>执行时间线</h2>
          <p className="muted">
            每条对应一个真实 Run；调用数在详情逐条显示，父子累计值不相加。
          </p>
          {!rows.length && !r.loading && (
            <p>暂无匹配运行记录。未持久化的阶段和模型内部 token 流不可见。</p>
          )}
          <div className="run-list">
            {visible.map((row) => {
              const bar = bars.find((b: { id: string }) => b.id === row.id);
              return (
                <button
                  className={`run-row ${selected === row.id ? "selected" : ""}`}
                  key={row.id}
                  onClick={() => setSelected(row.id)}
                >
                  <div>
                    <b>{row.component}</b>
                    <Badge status={row.status} />
                  </div>
                  <code>{row.id}</code>
                  <div className="track">
                    <span
                      style={{ left: `${bar?.left}%`, width: `${bar?.width}%` }}
                    />
                  </div>
                  <small>
                    {new Date(row.started_at).toLocaleString()} ·{" "}
                    {(row.duration_ms / 1000).toFixed(1)}s
                    {!row.ended_at ? " · 尚无结束记录" : ""}
                  </small>
                  {!!row.error && <p className="error">{pretty(row.error)}</p>}
                </button>
              );
            })}
          </div>
          <div className="section-head">
            <button
              disabled={!offset}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              上一页
            </button>
            <span>
              {rows.length ? offset + 1 : 0}–{offset + rows.length} /{" "}
              {r.data?.total ?? "—"}
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
          <h2>运行详情</h2>
          {!selected ? (
            <p className="muted">
              选择左侧记录，检查输入、输出、模型、usage、错误与预算统计。
            </p>
          ) : (
            <>
              <code>{selected}</code>
              <ResourceState {...detail} />
              {detail.data && (
                <>
                  <div className="toolbar">
                    <button
                      onClick={() =>
                        downloadJSON(`${selected}.json`, detail.data)
                      }
                    >
                      导出此 Run
                    </button>
                    {typeof detail.data.parent_run_id === "string" && (
                      <button
                        onClick={() =>
                          setSelected(String(detail.data?.parent_run_id))
                        }
                      >
                        查看父 Run
                      </button>
                    )}
                    {typeof detail.data.trace_id === "string" && (
                      <button
                        onClick={() => {
                          setTrace(String(detail.data?.trace_id));
                          setOffset(0);
                        }}
                      >
                        筛选同一 Trace
                      </button>
                    )}
                  </div>
                  {["metrics", "model", "error", "inputs", "outputs"].map(
                    (k) => (
                      <details key={k} open={["metrics", "error"].includes(k)}>
                        <summary>{k}</summary>
                        <Raw value={detail.data?.[k]} />
                      </details>
                    ),
                  )}
                  <p className="muted">
                    running
                    只表示数据库尚无终态；进程退出后可能遗留该状态。详情会屏蔽常见凭据并限制长字段。
                  </p>
                </>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
function TreeItem({
  item,
  onSelect,
}: {
  item: Tree;
  onSelect: (id: string, page: number | null) => void;
}) {
  return (
    <li>
      <button
        className="tree-item"
        onClick={() => onSelect(item.id, item.page)}
      >
        {item.label || item.text?.slice(0, 45) || item.type}{" "}
        {item.page !== null && <small>P{item.page + 1}</small>}
      </button>
      {!!item.children?.length && (
        <details>
          <summary>{item.children.length} 个子节点</summary>
          <ul>
            {item.children.map((c) => (
              <TreeItem key={c.id} item={c} onSelect={onSelect} />
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}
function DocumentView({
  base,
  doc,
  focus,
}: {
  base: string;
  doc: Doc;
  focus?: Evidence;
}) {
  const [page, setPage] = useState(0),
    [selected, setSelected] = useState(""),
    [overlay, setOverlay] = useState(true),
    [discarded, setDiscarded] = useState(false),
    [dpi, setDpi] = useState(100),
    [zoom, setZoom] = useState(100),
    [imageError, setImageError] = useState(false);
  const tree = useResource<{ root: Tree }>(`${base}/tree`),
    nodes = useResource<{ nodes: Node[] }>(
      `${base}/pages/${page}/nodes?include_discarded=${discarded}`,
    ),
    detail = useResource<Node>(
      selected
        ? `/api/v1/nodes/${selected.split("/").map(enc).join("/")}`
        : null,
    );
  useEffect(() => {
    if (focus) {
      setSelected(focus.node_id);
      const p = focus.page ?? refPage(focus.node_id);
      if (p !== undefined) setPage(p);
    }
  }, [focus]);
  useEffect(() => {
    if (
      focus &&
      detail.data?.node_id === focus.node_id &&
      detail.data.page !== null &&
      detail.data.page !== undefined
    )
      setPage(detail.data.page);
  }, [detail.data, focus]);
  const pageCount =
    Number(doc.quality.page_count_pdf || doc.quality.page_count_layout) || null;
  useEffect(() => setImageError(false), [base, page, dpi]);
  const select = (id: string, p: number | null) => {
    setSelected(id);
    if (p !== null) setPage(p);
  };
  return (
    <>
      <div className="section-head">
        <h1>文档与证据</h1>
        <Badge status={doc.status} />
      </div>
      <div className="doc-grid">
        <aside className="card">
          <h2>文档树</h2>
          <ResourceState {...tree} />
          {tree.data && (
            <ul>
              <TreeItem item={tree.data.root} onSelect={select} />
            </ul>
          )}
        </aside>
        <section className="card">
          <div className="toolbar">
            <button
              disabled={page === 0}
              onClick={() => {
                setPage((p) => p - 1);
                setSelected("");
              }}
            >
              上一页
            </button>
            <label>
              页码
              <input
                aria-label="页码"
                type="number"
                min="1"
                max={pageCount || undefined}
                value={page + 1}
                onChange={(e) => {
                  const p = Number(e.target.value);
                  if (
                    Number.isInteger(p) &&
                    p >= 1 &&
                    (!pageCount || p <= pageCount)
                  ) {
                    setPage(p - 1);
                    setSelected("");
                  }
                }}
              />
            </label>
            <button
              disabled={!!pageCount && page + 1 >= pageCount}
              onClick={() => {
                setPage((p) => p + 1);
                setSelected("");
              }}
            >
              下一页
            </button>
            <span>/ {pageCount || "未知"}</span>
            <label>
              <input
                type="checkbox"
                checked={overlay}
                onChange={(e) => setOverlay(e.target.checked)}
              />
              区域叠加
            </label>
            <label>
              <input
                type="checkbox"
                checked={discarded}
                onChange={(e) => setDiscarded(e.target.checked)}
              />
              discarded
            </label>
            <label>
              DPI
              <select
                value={dpi}
                onChange={(e) => setDpi(Number(e.target.value))}
              >
                {[72, 100, 144].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              缩放
              <select
                value={zoom}
                onChange={(e) => setZoom(Number(e.target.value))}
              >
                {[75, 100, 150, 200].map((v) => (
                  <option key={v} value={v}>
                    {v}%
                  </option>
                ))}
              </select>
            </label>
          </div>
          <ResourceState {...nodes} />
          {imageError && (
            <p role="alert" className="error">
              页面图加载失败，请检查后端 PDF 资产与页码。
            </p>
          )}
          <div className="paper-scroll">
            <div className="paper" style={{ width: `${zoom}%` }}>
              <img
                alt={`论文第 ${page + 1} 页`}
                src={`${base}/pages/${page}/image?dpi=${dpi}`}
                onError={() => setImageError(true)}
              />
              {overlay &&
                nodes.data?.nodes.flatMap((n) =>
                  n.regions
                    .filter((r) => r.page === page)
                    .map((r, i) => (
                      <button
                        key={`${n.node_id}-${i}`}
                        aria-label={n.label || n.node_id}
                        title={`${n.type} · ${n.label || n.node_id}${r.derived ? " · derived" : ""}`}
                        className={`region ${selected === n.node_id ? "active" : ""}`}
                        style={{
                          left: `${r.bbox.x0 * 100}%`,
                          top: `${r.bbox.y0 * 100}%`,
                          width: `${(r.bbox.x1 - r.bbox.x0) * 100}%`,
                          height: `${(r.bbox.y1 - r.bbox.y0) * 100}%`,
                        }}
                        onClick={() => setSelected(n.node_id)}
                      />
                    )),
                )}
            </div>
          </div>
        </section>
        <aside className="card">
          <h2>节点 Inspector</h2>
          <ResourceState {...detail} />
          {selected ? (
            <>
              <code>{selected}</code>
              {detail.data && (
                <>
                  <OcrContent node={obj(detail.data)} />
                  {focus?.node_id === selected && focus.char_span && (
                    <>
                      <h3>节点原文 / 证据位置</h3>
                      <p className="node-text">
                        {(() => {
                          const source = String(
                            obj(detail.data).text_raw ||
                              obj(detail.data).text_norm ||
                              "无正文",
                          );
                          const span =
                            focus?.node_id === selected
                              ? focus.char_span
                              : undefined;
                          const parts = evidenceTextParts(source, span);
                          if (parts)
                            return (
                              <>
                                {parts[0]}
                                <mark>{parts[1]}</mark>
                                {parts[2]}
                              </>
                            );
                          return source;
                        })()}
                      </p>
                    </>
                  )}
                  {focus?.cell && (
                    <p className="warning-banner">
                      来源表格单元格：{focus.cell.join(", ")}
                      。页面区域高亮到表格节点。
                    </p>
                  )}
                  <details>
                    <summary>节点原始数据</summary>
                    <Raw value={detail.data} />
                  </details>
                </>
              )}
            </>
          ) : (
            <p className="muted">
              点击解析区域，查看原文、坐标、质量标记和来源。
            </p>
          )}
        </aside>
      </div>
    </>
  );
}
function DebugPanel() {
  const logs = useSyncExternalStore(logStore.subscribe, logStore.getSnapshot),
    [errorsOnly, setErrorsOnly] = useState(false),
    [selected, setSelected] = useState<number | null>(null);
  const row = logs.find((l) => l.id === selected);
  const [copyState, setCopyState] = useState("");
  async function copyURL() {
    try {
      await navigator.clipboard.writeText(
        row ? new URL(row.path, location.origin).href : "",
      );
      setCopyState("已复制 URL");
    } catch {
      setCopyState("复制失败，请从下方选择 URL");
    }
  }
  return (
    <section className="card">
      <div className="section-head">
        <h1>请求调试台</h1>
        <div className="toolbar">
          <label>
            <input
              type="checkbox"
              checked={errorsOnly}
              onChange={(e) => setErrorsOnly(e.target.checked)}
            />
            仅失败
          </label>
          <button onClick={() => downloadJSON("drugpaper-network.json", logs)}>
            导出脱敏请求记录
          </button>
          <button onClick={logStore.clear}>清空</button>
        </div>
      </div>
      <p className="muted">
        当前浏览器会话最近 100 条 API 请求。HTTP 0
        为连接失败、超时或客户端中止；中止等待不会停止后端任务。图片请求请在浏览器
        Network 中检查。
      </p>
      <div className="split">
        <div className="network-list">
          {logs
            .filter((l) => !errorsOnly || l.outcome !== "success")
            .map((l) => (
              <button
                className="request-row"
                key={l.id}
                onClick={() => setSelected(l.id)}
              >
                <Badge
                  status={l.outcome !== "success" ? "failed" : "completed"}
                />
                <b>
                  {l.method} {l.status || "网络"} · {l.outcome}
                </b>
                <code>{l.path}</code>
                <small>
                  {l.ms} ms · {new Date(l.at).toLocaleTimeString()}
                </small>
              </button>
            ))}
          {!logs.length && <p>暂无请求，切换其他页面后返回查看。</p>}
        </div>
        <div>
          {row ? (
            <>
              <button onClick={() => void copyURL()}>复制请求 URL</button>
              <span role="status">{copyState}</span>
              <Raw value={row} />
            </>
          ) : (
            <p className="muted">选择请求，查看完整 URL、响应和错误详情。</p>
          )}
        </div>
      </div>
    </section>
  );
}
function Actions({ base }: { base: string }) {
  const [enabled, setEnabled] = useState(false),
    [action, setAction] = useState("reader"),
    [from, setFrom] = useState(""),
    [force, setForce] = useState(false),
    [prepare, setPrepare] = useState(true),
    [question, setQuestion] = useState(""),
    [confirming, setConfirming] = useState(false),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<unknown>(),
    [error, setError] = useState("");
  const numeric = from === "" ? undefined : Number(from) - 1;
  const invalid =
    numeric !== undefined && (!Number.isInteger(numeric) || numeric < 0);
  const body =
    action === "lookups"
      ? { questions: [{ question, kind: "text" }] }
      : action === "full-text"
        ? {
            prepare_text: prepare,
            force,
            ...(numeric === undefined ? {} : { from_page: numeric }),
          }
        : action === "exports"
          ? {}
          : { ...(numeric === undefined ? {} : { from_page: numeric }) };
  async function run() {
    setConfirming(false);
    setBusy(true);
    setError("");
    setResult(undefined);
    try {
      setResult(
        await request(`${base}/${action}`, {
          method: "POST",
          body,
          timeout: 600000,
        }),
      );
    } catch (e) {
      setError(`${String(e)}。请求结束不代表后端已停止，请在运行监控中核对。`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card">
      <h1>受控调试操作</h1>
      <p>
        这些操作调用现有后端，可能产生模型费用及新的数据库记录。默认关闭；页面采用
        1 起始页码，发送前转换为后端的 0 起始页码。
      </p>
      <label>
        <input
          type="checkbox"
          checked={enabled}
          disabled={busy}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        启用本会话写操作
      </label>
      <div className="filters">
        <label>
          操作
          <select
            disabled={busy}
            value={action}
            onChange={(e) => setAction(e.target.value)}
          >
            <option value="reader">顺序阅读 / 指定页重读</option>
            <option value="full-text">完整全文理解</option>
            <option value="lookups">只读内容回查（产生运行记录）</option>
            <option value="exports">生成 ZIP 导出</option>
          </select>
        </label>
        {["reader", "full-text"].includes(action) && (
          <label>
            起始页（留空恢复）
            <input
              disabled={busy}
              type="number"
              min="1"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </label>
        )}
        {action === "full-text" && (
          <>
            <label>
              <input
                disabled={busy}
                type="checkbox"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
              />
              force
            </label>
            <label>
              <input
                disabled={busy}
                type="checkbox"
                checked={prepare}
                onChange={(e) => setPrepare(e.target.checked)}
              />
              prepare_text
            </label>
          </>
        )}
        {action === "lookups" && (
          <label>
            问题
            <input
              disabled={busy}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
          </label>
        )}
      </div>
      <button
        className="primary"
        disabled={
          !enabled ||
          busy ||
          invalid ||
          (action === "lookups" && !question.trim())
        }
        onClick={() => setConfirming(true)}
      >
        {busy ? "后端执行中，可切换运行监控查看…" : "预览并确认请求"}
      </button>
      {invalid && <p role="alert">起始页必须是正整数。</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {result !== undefined && (
        <>
          <h2>操作响应</h2>
          <Raw value={result} />
          {!!result && typeof result === "object" && "export_id" in result && (
            <a
              href={`/api/v1/exports/${enc(String(result.export_id))}/download`}
            >
              下载导出 ZIP
            </a>
          )}
        </>
      )}
      {confirming && (
        <div className="modal-backdrop">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            className="modal card"
          >
            <h2 id="confirm-title">确认调用后端</h2>
            <code>
              POST {base}/{action}
            </code>
            <Raw value={body} />
            <p>
              当前接口没有任务取消能力。切换页面或关闭浏览器不会取消后端执行。
            </p>
            <div className="toolbar">
              <button onClick={() => setConfirming(false)}>返回</button>
              <button className="primary" onClick={() => void run()}>
                确认执行
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
function App() {
  const [tab, setTab] = useState(
      (() => {
        const v = new URLSearchParams(location.search).get("view") || "runs";
        return [
          "workflow",
          "layout",
          "figure-debug",
          "molecule-debug",
          "skill-editor",
          "runs",
          "document",
          "compounds",
          "measurements",
          "figures",
          "search",
          "result",
          "notes",
          "actions",
          "capabilities",
          "batch",
          "debug",
        ].includes(v)
          ? v
          : "runs";
      })(),
    ),
    [docId, setDocId] = useState(
      () => new URLSearchParams(location.search).get("doc") || "",
    ),
    [poll, setPoll] = useState(3000),
    [paused, setPaused] = useState(false),
    [hidden, setHidden] = useState(document.hidden);
  const [focus, setFocus] = useState<Evidence | undefined>(),
    [focusRun, setFocusRun] = useState(""),
    [navigationError, setNavigationError] = useState("");
  const navigateEvidence = (e: Evidence) => {
    const match = e.node_id.match(/^([^/]+)\/r(\d+)\//);
    if (!match || match[1] !== docId) {
      setNavigationError("该证据不属于当前文档，未跳转。");
      return;
    }
    if (!doc?.active_revision) {
      setNavigationError("当前文档尚无可核对的解析版本，请先刷新文档。");
      return;
    }
    if (Number(match[2]) !== doc.active_revision) {
      setNavigationError(
        `该证据属于历史 revision ${match[2]}，当前为 ${doc.active_revision}。请核对原始产物版本。`,
      );
      return;
    }
    setNavigationError("");
    setFocus(e);
    setTab("document");
  };
  const navigateRun = (id: string) => {
    setFocusRun(id);
    setTab("runs");
  };
  useEffect(() => {
    setFocus(undefined);
    setFocusRun("");
    setNavigationError("");
  }, [docId]);
  const interval = paused || hidden ? 0 : poll;
  const docs = useResource<{ documents: Doc[] }>("/api/v1/documents", interval),
    health = useResource<{ status: string; version: string }>(
      "/healthz",
      interval ? 15000 : 0,
    );
  const base = `/api/v1/documents/${enc(docId)}`;
  const listedDoc = docs.data?.documents.find((d) => d.document_id === docId);
  const focusedDoc = useResource<Doc>(
    docId && docs.data && !listedDoc ? base : null,
    interval,
  );
  const doc = listedDoc || focusedDoc.data;
  useEffect(() => {
    const fn = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", fn);
    return () => document.removeEventListener("visibilitychange", fn);
  }, []);
  useEffect(() => {
    const url = new URL(location.href);
    if (docId) url.searchParams.set("doc", docId);
    else url.searchParams.delete("doc");
    url.searchParams.set("view", tab);
    history.replaceState(null, "", url);
  }, [docId, tab]);
  const [uploading, setUploading] = useState(false),
    [uploadError, setUploadError] = useState("");
  const [parseMode, setParseMode] = useState("manual");
  const fileRef = useRef<HTMLInputElement>(null);
  async function upload(file: File) {
    setUploading(true);
    setUploadError("");
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await request<{ document_id: string }>(
        `/api/v1/documents?mode=${parseMode}`,
        { method: "POST", body: form, timeout: 600000 },
      );
      setDocId(result.document_id);
      setTab("workflow");
      docs.refresh();
    } catch (e) {
      setUploadError(String(e));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon">D</span>
          <div>
            Drugpaper<small>论文理解 · 开发工作台</small>
          </div>
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav>
          {[
            ["workflow", "▷", "全流程工作区"],
            ["layout", "▤", "MinerU 布局"],
            ["figure-debug", "▦", "图表逐步解析"],
            ["molecule-debug", "⬡", "分子检测与修正"],
            ["skill-editor", "⚙", "局部 Skill 编辑"],
            ["runs", "◉", "运行监控"],
            ["document", "▤", "文档与证据"],
            ["compounds", "⬡", "化合物台账"],
            ["measurements", "▥", "测定数据"],
            ["figures", "▦", "图像报告"],
            ["search", "⌕", "论文检索"],
            ["result", "◇", "全文结果"],
            ["notes", "≡", "阅读检查点"],
            ["actions", "▷", "调试操作"],
            ["capabilities", "⚙", "能力与版本"],
            ["batch", "▣", "批次状态"],
            ["debug", "⌘", "请求调试台"],
          ].map(([id, icon, label]) => (
            <button
              key={id}
              aria-label={label}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              <span>{icon}</span>
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <Badge
            status={
              health.error ? "failed" : health.data?.status || "connecting"
            }
          />
          <p>
            {health.error
              ? "后端连接异常"
              : `API ${health.data?.version || "—"}`}
          </p>
          <a href="/api/v1/skills" target="_blank" rel="noreferrer">
            能力目录 JSON ↗
          </a>
          <p>
            <a href="/docs" target="_blank" rel="noreferrer">
              API 调试文档 ↗
            </a>
          </p>
          <p>本地联调版 · P6 / P8 early</p>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <div>
            <span className="eyebrow">DEVELOPER WORKBENCH</span>
            <h2>论文理解与运行诊断</h2>
          </div>
          <div className="toolbar">
            <label>
              刷新间隔
              <select
                value={poll}
                onChange={(e) => setPoll(Number(e.target.value))}
              >
                <option value="1000">1 秒</option>
                <option value="3000">3 秒</option>
                <option value="10000">10 秒</option>
              </select>
            </label>
            <button onClick={() => setPaused(!paused)}>
              {paused ? "恢复轮询" : "暂停轮询"}
            </button>
            <span className="muted">
              {hidden ? "后台暂停" : paused ? "已暂停" : "轮询中"}
            </span>
          </div>
        </header>
        <div className="document-bar">
          <label>
            当前论文
            <select
              aria-label="当前论文"
              value={docId}
              onChange={(e) => setDocId(e.target.value)}
            >
              <option value="">选择文档</option>
              {doc && !listedDoc && (
                <option value={doc.document_id}>
                  {doc.title || doc.filename || doc.document_id}
                </option>
              )}
              {docs.data?.documents.map((d) => (
                <option value={d.document_id} key={d.document_id}>
                  {d.title || d.filename || d.document_id}
                </option>
              ))}
            </select>
          </label>
          <button onClick={docs.refresh}>刷新列表</button>
          <label>
            解析模式
            <select
              aria-label="解析模式"
              value={parseMode}
              onChange={(e) => setParseMode(e.target.value)}
            >
              <option value="manual">手动：仅 MinerU，选图后解析</option>
              <option value="auto">自动：MinerU + 全部图表</option>
            </select>
          </label>
          <label className="upload">
            {uploading
              ? "导入中…"
              : parseMode === "manual"
                ? "上传 PDF（仅解析）"
                : "上传 PDF（自动全流程）"}
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf"
              disabled={uploading}
              onChange={(e) => {
                if (e.target.files?.[0]) void upload(e.target.files[0]);
              }}
            />
          </label>
          {doc && (
            <>
              <Badge status={doc.status} />
              {doc.status === "ready" && (
                <span className="muted">
                  解析完成 · 图表提取情况请查看图像报告
                </span>
              )}
              <span className="muted">
                revision {doc.active_revision ?? "—"}
              </span>
            </>
          )}
        </div>
        <main>
          <ResourceState {...docs} />
          <ResourceState {...focusedDoc} />
          {uploadError && (
            <p className="error" role="alert">
              {uploadError}
            </p>
          )}
          {!!doc?.error && (
            <div className="error">文档错误：{pretty(doc.error)}</div>
          )}
          {navigationError && (
            <p className="error" role="alert">
              {navigationError}
            </p>
          )}
          {tab === "debug" ? (
            <DebugPanel />
          ) : tab === "capabilities" ? (
            <CapabilitiesView />
          ) : tab === "batch" ? (
            <BatchView interval={interval} />
          ) : !docId ? (
            <div className="empty card">
              <h1>从一篇论文开始诊断</h1>
              <p>
                选择已有文档，或上传
                PDF。运行记录、证据和调试请求会关联到同一文档。
              </p>
              <p className="muted">
                当前已接 P2–P6 接口。对话流、纠错预览、用户及模型管理等待 P7/P8
                后端。
              </p>
            </div>
          ) : (
            <div key={docId}>
              <div hidden={tab !== "actions"}>
                <Actions base={base} />
              </div>
              {[
                "workflow",
                "layout",
                "figure-debug",
                "molecule-debug",
                "skill-editor",
              ].includes(tab) &&
                doc && (
                  <WorkbenchView
                    base={base}
                    doc={doc}
                    section={tab}
                    interval={interval}
                  />
                )}
              {tab === "runs" && (
                <RunMonitor
                  base={base}
                  interval={interval}
                  focusRun={focusRun}
                />
              )}
              {tab === "document" &&
                (doc ? (
                  <DocumentView base={base} doc={doc} focus={focus} />
                ) : (
                  <p>所选文档不在当前列表中，请刷新或重新选择。</p>
                ))}
              {tab === "compounds" && (
                <CompoundsView base={base} onEvidence={navigateEvidence} />
              )}
              {tab === "measurements" && (
                <MeasurementsView base={base} onEvidence={navigateEvidence} />
              )}
              {tab === "search" && (
                <SearchView base={base} onEvidence={navigateEvidence} />
              )}
              {tab === "figures" && (
                <FiguresView
                  base={base}
                  onEvidence={navigateEvidence}
                  onRun={navigateRun}
                />
              )}
              {tab === "result" && (
                <ResultsView
                  base={base}
                  onEvidence={navigateEvidence}
                  onRun={navigateRun}
                />
              )}
              {tab === "notes" && (
                <NotesView
                  base={base}
                  interval={interval}
                  onEvidence={navigateEvidence}
                />
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
