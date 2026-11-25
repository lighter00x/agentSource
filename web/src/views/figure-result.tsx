import { Badge, Raw, ResourceState, useResource } from "../ui";
import { downloadJSON } from "../debug.mjs";

type Result = {
  schema_version?: string;
  node_ref: string;
  run_id: string;
  processing_stage: string;
  status: string;
  extraction: Record<string, unknown>;
  pathways?: unknown[];
};
export function FigureResultFile({
  endpoint,
  interval = 0,
}: {
  endpoint: string;
  interval?: number;
}) {
  const result = useResource<Result>(endpoint, interval);
  const summary =
    result.data?.schema_version?.startsWith("flowchart-summary/") || false;
  const compact =
    summary || result.data?.schema_version === "flowchart-result/1.0";
  const filename = compact
    ? `${endpoint.split("/").at(-2)}-flowchart.json`
    : `${result.data?.node_ref?.split("/").at(-1)}-${result.data?.run_id}-result.json`;
  return (
    <section className="result-file">
      <h3>
        {result.data?.processing_stage === "backfilled"
          ? "回填预览（待检查）"
          : "最终解析结果"}
      </h3>
      <ResourceState {...result} />
      {(result.data?.extraction || result.data?.pathways) && (
        <>
          <p>
            <Badge status={result.data.status} />{" "}
            {summary
              ? "精简过程结果：直接保留反应物、产物、全部实际成员和拼接后的 SMILES；必要的化学取代位号保留，理论候选、PDF 坐标与调试信息另存中间文件。"
              : compact
                ? "流程精简结果：保留反应物、生成物、通式和反应条件；详细调试信息见各阶段。"
                : result.data.processing_stage === "backfilled"
                  ? "已回填，后续检查尚未完成"
                  : "已生成报告；请同时查看结果中的检查状态"}
          </p>
          <a
            className="button"
            href={`${endpoint}?download=true`}
            download={filename.replace(/[^a-zA-Z0-9_.-]/g, "_")}
            onClick={(event) => {
              event.preventDefault();
              downloadJSON(
                filename.replace(/[^a-zA-Z0-9_.-]/g, "_"),
                result.data,
              );
            }}
          >
            下载结果 JSON
          </a>
          {compact && (
            <span className="actions">
              <a
                className="button secondary"
                href={`${endpoint}?view=delivery&download=true`}
              >
                下载完整化学信息
              </a>
              <a
                className="button secondary"
                href={`${endpoint}?view=full&download=true`}
              >
                下载详细中间文件
              </a>
            </span>
          )}
          <details open>
            <summary>最终内容 · JSON 代码查看</summary>
            <Raw value={compact ? result.data : result.data.extraction} />
          </details>
          {!compact && (
            <details>
              <summary>文件来源与检查信息</summary>
              <Raw value={result.data} />
            </details>
          )}
        </>
      )}
    </section>
  );
}
