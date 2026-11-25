import { ProcessJSON } from "./process-content";
type Data = Record<string, unknown>;
type Event = { kind: string; data: unknown };
const obj = (v: unknown): Data =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Data) : {};
export function StructureInputs({ events }: { events: Event[] }) {
  const requests = events.filter(
    (e) =>
      e.kind === "model_request" && obj(e.data).task === "figure_structure",
  );
  if (!requests.length) return null;
  return (
    <section className="structure-inputs" aria-label="结构化实际图像输入">
      <h4>结构化实际图像输入</h4>
      <p className="muted">
        取自实际模型请求。编号目标图用于对应分子与
        mol_id，未标注原图和关联文献图只作辅助核对。
      </p>
      {requests.map((event, index) => {
        const req = obj(obj(event.data).request),
          messages = Array.isArray(req.messages) ? req.messages : [];
        const images: { ref: string; role: string }[] = [];
        let role = "目标输入图（历史记录未标注用途）";
        for (const value of messages) {
          const message = obj(value);
          if (message.role !== "user" || !Array.isArray(message.content))
            continue;
          for (const value of message.content) {
            const part = obj(value),
              text = String(part.text || "");
            if (text.startsWith("目标编号图"))
              role = "目标编号图 · 用于 mol_id 对应";
            else if (text.startsWith("辅助原图"))
              role = "辅助原图 · 不替代编号图";
            else if (text.startsWith("Related evidence image"))
              role = "关联文献参考图";
            if (typeof part.blob_ref === "string")
              images.push({ ref: part.blob_ref, role });
          }
        }
        return (
          <section key={index}>
            <h5>
              第 {index + 1} 次结构化提取请求 · {images.length} 张图像
            </h5>
            <div className="structure-input-gallery">
              {images.map((im, i) => (
                <figure key={i}>
                  <figcaption>
                    输入图 {i + 1} · {im.role}
                  </figcaption>
                  <a
                    href={`/api/v1/blobs/${encodeURIComponent(im.ref)}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <img
                      loading="lazy"
                      src={`/api/v1/blobs/${encodeURIComponent(im.ref)}`}
                      alt={`结构化输入图 ${i + 1}：${im.role}`}
                    />
                  </a>
                </figure>
              ))}
            </div>
            <ProcessJSON title="此结构化请求的原始 JSON" value={event.data} />
          </section>
        );
      })}
    </section>
  );
}
