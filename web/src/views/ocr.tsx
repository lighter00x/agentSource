import katex from "katex";
import { Raw } from "../ui";
import "katex/dist/katex.min.css";

type Span = { type?: string; content?: string };
type Line = { spans: Span[] };

function Formula({
  source,
  display = false,
}: {
  source: string;
  display?: boolean;
}) {
  const tex = source
    .trim()
    .replace(/^\$\$([\s\S]*)\$\$$/, "$1")
    .replace(/^\$([^$]*)\$$/, "$1")
    .replace(/^\\\(([\s\S]*)\\\)$/, "$1")
    .replace(/^\\\[([\s\S]*)\\\]$/, "$1");
  try {
    const html = katex.renderToString(tex, {
      displayMode: display,
      throwOnError: true,
      trust: false,
      maxExpand: 1000,
      maxSize: 20,
    });
    return (
      <span
        className="ocr-formula"
        title={source}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  } catch (error) {
    return (
      <span className="ocr-formula-error" title={String(error)}>
        <code>{source}</code>
        <small>（公式渲染失败，保留源码）</small>
      </span>
    );
  }
}

// Only explicit delimiters are interpreted in untyped OCR; plain text stays literal.
function MarkedText({ text }: { text: string }) {
  const pattern =
    /(?<!\\)(\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$)/g;
  const parts = [];
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    parts.push(text.slice(end, match.index));
    parts.push(
      <Formula
        key={match.index}
        source={match[2] ?? match[3] ?? match[4] ?? match[5]}
        display={match[2] !== undefined || match[3] !== undefined}
      />,
    );
    end = match.index! + match[0].length;
  }
  parts.push(text.slice(end));
  return <>{parts}</>;
}

export function OcrContent({ node }: { node?: Record<string, unknown> }) {
  const text = String(node?.text_raw || node?.text_norm || "无正文");
  const attrs = node?.attributes as Record<string, unknown> | undefined;
  const lines = attrs?.ocr_lines as Line[] | undefined;
  return (
    <div className="ocr-content">
      <h3>OCR 排版预览（含公式）</h3>
      {attrs?.ocr_render_warning ? (
        <p className="warning-banner">{String(attrs.ocr_render_warning)}</p>
      ) : null}
      <div className="node-text ocr-preview">
        {lines?.length ? (
          lines.map((line, i) => (
            <span key={i}>
              {i > 0 ? "\n" : ""}
              {line.spans.map((span, j) =>
                span.type === "inline_equation" ||
                span.type === "interline_equation" ||
                span.type === "equation" ? (
                  <Formula
                    key={j}
                    source={span.content || ""}
                    display={
                      node?.type === "formula" ||
                      span.type !== "inline_equation"
                    }
                  />
                ) : (
                  <MarkedText key={j} text={span.content || ""} />
                ),
              )}
            </span>
          ))
        ) : node?.type === "formula" ? (
          <Formula source={text} display />
        ) : (
          <MarkedText text={text} />
        )}
      </div>
      <details>
        <summary>原始 OCR / 公式源码</summary>
        <pre className="node-text">{text}</pre>
        {lines?.length ? <Raw value={lines} /> : null}
      </details>
    </div>
  );
}
