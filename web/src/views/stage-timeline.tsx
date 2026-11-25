import { invocationTrace } from "../invocation-trace.mjs";
import { toolLabels } from "../agent-trace.mjs";
import { ProcessContent, ProcessJSON } from "./process-content";

type Data = Record<string, unknown>;
type Event = { kind: string; data: unknown; at?: string };
const obj = (v: unknown): Data =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Data) : {};
const titles: Record<string, string> = {
  visual_classify: "判断图表类型",
  molecule_detect: "检测分子区域",
  molecule_recognize: "识别分子结构",
  som_render: "生成编号图",
  scheme_extract: "提取合成路线",
  table_extract: "提取表格",
  mol_backfill: "回填分子信息",
  measurement_map: "映射测定指标",
  upstream_reuse: "复用上游结果",
  manual_reuse: "沿用人工修正",
  transport_retry: "重试模型请求",
  transport_error: "模型请求错误",
};
export function StageTimeline({
  events,
  running,
  inherited,
}: {
  events: Event[];
  running: boolean;
  inherited: boolean;
}) {
  const rows = invocationTrace(events) as {
    type: string;
    name: string;
    sequence: number;
    round: number;
    start: Event | null;
    result?: Event | null;
    transport: Event[];
  }[];
  return (
    <section className="stage-timeline" aria-label="步骤调用过程">
      <h4>实际调用过程</h4>
      <p className="muted">
        按调用开始的顺序展示。输入与返回内容直接显示，原始请求体和回答 JSON
        可按需展开。{inherited && "这里回放的是来源运行保存的过程。"}
      </p>
      {!rows.length && (
        <p className="muted">
          {running
            ? "等待调用记录…"
            : "此步骤未保存模型或工具调用记录；不代表发生过调用。"}
        </p>
      )}
      <ol className="invocation-list">
        {rows.map((row, i) => {
          const input = obj(row.start?.data),
            output = obj(row.result?.data);
          const failed =
            row.result?.kind.endsWith("error") ||
            output.ok === false ||
            obj(output.result).ok === false ||
            ["failed", "error"].includes(String(obj(output.result).status));
          const state =
            row.type === "record"
              ? "已记录"
              : row.result
                ? failed
                  ? "调用失败"
                  : "已返回"
                : running
                  ? "等待返回"
                  : "未记录到返回";
          const name =
            titles[row.name] ||
            (toolLabels as Record<string, string>)[row.name] ||
            row.name;
          const returned =
            row.type === "model"
              ? (output.parsed ?? output.content)
              : row.type === "tool"
                ? (output.result ?? output.error)
                : output.payload;
          const request = obj(input.request);
          return (
            <li
              className={`invocation-card ${failed ? "failed" : ""}`}
              key={row.sequence}
            >
              <header>
                <span className="agent-step-number">{i + 1}</span>
                <h5>
                  {row.type === "model"
                    ? `第 ${row.round} 轮 · 模型调用`
                    : row.type === "skill"
                      ? "Skill 调用"
                      : row.type === "tool"
                        ? `${row.round ? `第 ${row.round} 轮 · ` : ""}工具调用`
                        : "过程记录"}{" "}
                  · {name}
                </h5>
                <span className="stage-status">{state}</span>
              </header>
              <p className="muted">
                <code>{row.name}</code>
                {input.model ? ` · 模型 ${String(input.model)}` : ""}
                {row.start?.at ? ` · ${row.start.at}` : ""}
              </p>
              {row.type === "record" ? (
                <ProcessContent value={input} />
              ) : (
                <>
                  <div className="invocation-input">
                    <h6>{row.type === "model" ? "请求内容" : "调用输入"}</h6>
                    {!row.start ? (
                      <p className="muted">未记录调用输入。</p>
                    ) : row.type === "model" ? (
                      <>
                        {Array.isArray(request.messages) ? (
                          <div className="process-messages">
                            {request.messages.map((v, j) => {
                              const m = obj(v);
                              return (
                                <section key={j}>
                                  <strong>
                                    {(
                                      {
                                        system: "系统指令",
                                        user: "用户消息",
                                        assistant: "模型消息",
                                        tool: "工具消息",
                                      } as Record<string, string>
                                    )[String(m.role)] ||
                                      String(m.role || "消息")}
                                    {m.name ? ` · ${String(m.name)}` : ""}
                                  </strong>
                                  <ProcessContent value={m.content} />
                                  {m.tool_calls ? (
                                    <ProcessContent
                                      value={{ tool_calls: m.tool_calls }}
                                    />
                                  ) : null}
                                </section>
                              );
                            })}
                          </div>
                        ) : (
                          <ProcessContent value={request} />
                        )}
                        {Array.isArray(request.tools) && (
                          <p>
                            可用工具：
                            {request.tools
                              .map((t) => String(obj(t).name))
                              .join("、") || "无"}
                          </p>
                        )}
                      </>
                    ) : (
                      <ProcessContent
                        value={input.arguments ?? input.inputs ?? input}
                      />
                    )}
                    {row.type === "skill" && input.target != null && (
                      <ProcessContent value={{ target: input.target }} />
                    )}
                    {row.type === "skill" && input.images != null && (
                      <>
                        <h6>实际输入图像</h6>
                        <ProcessContent value={input.images} />
                      </>
                    )}
                  </div>
                  <div className="invocation-output">
                    <h6>返回内容</h6>
                    {row.result ? (
                      <>
                        <ProcessContent
                          value={
                            returned ??
                            (row.type === "model" && !failed
                              ? "模型未返回文字内容。"
                              : output)
                          }
                        />
                        {row.type === "model" &&
                          Array.isArray(output.tool_calls) &&
                          output.tool_calls.length > 0 && (
                            <>
                              <h6>选择调用的工具</h6>
                              <ProcessContent value={output.tool_calls} />
                            </>
                          )}
                      </>
                    ) : (
                      <p className="muted">{state}</p>
                    )}
                  </div>
                  <ProcessJSON
                    title="原始请求体 JSON"
                    value={row.start?.data ?? null}
                  />
                  <ProcessJSON
                    title="原始回答 JSON"
                    value={row.result?.data ?? null}
                  />
                  {row.transport
                    .filter((e) =>
                      ["transport_error", "transport_retry"].includes(e.kind),
                    )
                    .map((e, j) => (
                      <section key={j} className="invocation-retry">
                        <h6>{titles[e.kind]}</h6>
                        <ProcessContent value={e.data} />
                      </section>
                    ))}
                  {!!row.transport.length && (
                    <details className="process-transport">
                      <summary>
                        底层请求与重试 · {row.transport.length} 条记录
                      </summary>
                      {row.transport.map((e, j) => (
                        <section key={j}>
                          <strong>{titles[e.kind] || e.kind}</strong>
                          {["transport_error", "transport_retry"].includes(
                            e.kind,
                          ) && <ProcessContent value={e.data} />}
                          <ProcessJSON title="原始传输 JSON" value={e.data} />
                        </section>
                      ))}
                    </details>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
