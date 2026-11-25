import { useEffect, useState } from "react";
import {
  evidence,
  text,
  nodePath,
  type Navigate,
  type Box,
} from "../contracts";
export function EvidenceLinks({
  items,
  onEvidence,
}: {
  items: unknown;
  onEvidence: Navigate;
}) {
  const refs = evidence(items);
  return (
    <span className="evidence-links">
      {refs.length ? (
        refs.map((e, i) => (
          <button
            className="evidence-link"
            key={JSON.stringify(e)}
            title={e.node_id}
            onClick={() => onEvidence(e)}
          >
            证据 {i + 1}
            {e.cell ? ` · 单元格 ${e.cell.join(",")}` : ""}
          </button>
        ))
      ) : (
        <small>未提供节点证据</small>
      )}
    </span>
  );
}
export function Crop({
  nodeRef,
  label = "原图",
  dpi = 144,
  highlight,
}: {
  nodeRef: string;
  label?: string;
  dpi?: number;
  highlight?: Box | null;
}) {
  const [failed, setFailed] = useState(false),
    [retry, setRetry] = useState(0);
  const src = nodePath(nodeRef) + `/crop?dpi=${dpi}&pad=0`;
  useEffect(() => setFailed(false), [src, retry]);
  return (
    <figure className="source-image">
      {failed ? (
        <div role="alert">
          原图暂时无法加载{" "}
          <button onClick={() => setRetry((x) => x + 1)}>重试</button>
        </div>
      ) : (
        <div className="crop-canvas">
          <img
            key={`${src}:${retry}`}
            alt={label}
            src={`${src}&retry=${retry}`}
            onError={() => setFailed(true)}
            loading="lazy"
          />
          {highlight && (
            <span
              aria-label="原图单元格高亮"
              className="cell-highlight"
              style={{
                left: `${highlight.x0 * 100}%`,
                top: `${highlight.y0 * 100}%`,
                width: `${(highlight.x1 - highlight.x0) * 100}%`,
                height: `${(highlight.y1 - highlight.y0) * 100}%`,
              }}
            />
          )}
        </div>
      )}
      <figcaption>
        {label} · <code>{nodeRef}</code>
      </figcaption>
    </figure>
  );
}
export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}
export function Warnings({ items }: { items: unknown }) {
  const warnings = Array.isArray(items) ? items : [];
  return warnings.length ? (
    <ul className="warning-list">
      {warnings.map((v, i) => (
        <li key={i}>{text(v)}</li>
      ))}
    </ul>
  ) : null;
}
