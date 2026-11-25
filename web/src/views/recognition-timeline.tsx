import { recognitionTrace } from "../recognition-trace.mjs";
import { ProcessContent, ProcessJSON } from "./process-content";
import { Molecule } from "./molecule";
import { BBoxCrop } from "./bbox-crop";
type Data = Record<string, unknown>;
type Event = { kind: string; data: unknown; at?: string };
const obj = (v: unknown): Data =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Data) : {};
export function RecognitionTimeline({
  events,
  detectionEvents,
  structures,
  running,
}: {
  events: Event[];
  detectionEvents: Event[];
  structures: unknown[];
  running: boolean;
}) {
  const cards = recognitionTrace(events, detectionEvents, structures);
  return (
    <section className="recognition-timeline" aria-label="逐个分子识别过程">
      <h4>逐个分子识别</h4>
      <p className="muted">
        按检测编号对应裁剪区域、实际送入识别工具的裁剪图与返回结构。编号沿用检测结果；历史手动修正的结构单独标明。
      </p>
      {!cards.length && <p className="muted">尚未保存逐个分子的识别记录。</p>}
      <ol className="recognition-list">
        {cards.map((card, i) => {
          const structure = obj(card.structure),
            validation = obj(structure.validation),
            raw = obj(card.raw_result);
          const smiles = structure.canonical_smiles || structure.smiles_core;
          const error = validation.error || raw.error;
          const state = card.manual_override
            ? "已重识别，最终沿用历史手动修正"
            : card.source === "cache"
              ? "沿用识别缓存"
              : card.source === "manual"
                ? "沿用历史手动修正"
                : card.source === "unrecorded"
                  ? "未记录识别调用"
                  : error || validation.valid === false
                    ? "识别需复核"
                    : card.complete
                      ? "已返回"
                      : running
                        ? "识别中"
                        : "未记录到返回";
          return (
            <li className="recognition-card" key={`${card.node_ref}-${i}`}>
              <header>
                <h5>
                  分子编号{" "}
                  {typeof card.mol_id === "number" ? card.mol_id : "未记录"}
                </h5>
                <span className="stage-status">{state}</span>
              </header>
              <p className="muted">
                <code>{String(card.node_ref || "目标未记录")}</code>
                {typeof card.segment === "number"
                  ? ` · 图像片段 ${card.segment}`
                  : ""}
              </p>
              {card.manual_override && (
                <p className="stage-reuse">
                  下方展示本次重识别的返回；最终结果仍采用历史手动修正的结构。
                </p>
              )}
              {card.bbox && (
                <div className="recognition-region">
                  <strong>上一步检测区域（相对原图坐标）</strong>
                  <p>
                    {(() => {
                      const b = obj(card.bbox);
                      return [b.x0, b.y0, b.x1, b.y1].every(
                        (v) => typeof v === "number",
                      )
                        ? `左上 (${(Number(b.x0) * 100).toFixed(1)}%, ${(Number(b.y0) * 100).toFixed(1)}%) → 右下 (${(Number(b.x1) * 100).toFixed(1)}%, ${(Number(b.y1) * 100).toFixed(1)}%)`
                        : "坐标格式未识别，详见原始记录。";
                    })()}
                  </p>
                </div>
              )}
              <div className="recognition-pair">
                <section>
                  <h6>
                    {card.crop_ref
                      ? "实际送入工具的 bbox 裁剪图"
                      : "检测 bbox 裁剪图"}
                  </h6>
                  {card.crop_ref ? (
                    <figure>
                      <a
                        href={`/api/v1/blobs/${encodeURIComponent(card.crop_ref)}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <img
                          src={`/api/v1/blobs/${encodeURIComponent(card.crop_ref)}`}
                          alt={`分子 ${card.mol_id ?? "未知编号"} 的识别裁剪图`}
                          loading="eager"
                        />
                      </a>
                      <figcaption>
                        工具：molparser.recognize · 点击查看原图
                      </figcaption>
                    </figure>
                  ) : card.source_image_ref && card.bbox ? (
                    <figure>
                      <BBoxCrop
                        sourceRef={card.source_image_ref}
                        bbox={card.bbox}
                        label={`分子 ${card.mol_id ?? "未知编号"} 的 bbox 裁剪图`}
                      />
                      <figcaption>
                        按本次检测 bbox 从原图直接裁剪。
                        {card.source === "manual"
                          ? "本轮沿用历史手动修正，未重新调用识别工具。"
                          : card.source === "cache"
                            ? "本轮使用识别缓存，未重新调用识别工具。"
                            : "此图为检测区域预览，未保存本轮工具发送记录。"}
                      </figcaption>
                    </figure>
                  ) : (
                    <p className="muted">
                      {card.source === "manual"
                        ? "此分子沿用历史手动修正结构，本轮未向识别工具发送图像。"
                        : "该历史记录未保存可对应的裁剪图，不使用整图代替。"}
                    </p>
                  )}
                </section>
                <section>
                  <h6>
                    {card.source === "manual" ? "历史手动修正结构" : "识别返回"}
                  </h6>
                  {typeof smiles === "string" && smiles ? (
                    <>
                      <Molecule smiles={smiles} />
                      <p>
                        <strong>SMILES</strong> <code>{smiles}</code>
                      </p>
                    </>
                  ) : (
                    <p className="muted">尚无可绘制的有效 SMILES。</p>
                  )}
                  {Boolean(structure.formula) && (
                    <p>分子式：{String(structure.formula)}</p>
                  )}
                  {Boolean(structure.e_smiles_raw) && (
                    <p>
                      原始扩展 SMILES：
                      <code>{String(structure.e_smiles_raw)}</code>
                    </p>
                  )}
                  {validation.valid !== undefined && (
                    <p>SMILES 解析：{validation.valid ? "通过" : "未通过"}</p>
                  )}
                  {Boolean(error) && <p className="error">{String(error)}</p>}
                </section>
              </div>
              {card.raw_result && (
                <details>
                  <summary>工具返回的全部字段</summary>
                  <ProcessContent value={card.raw_result} />
                </details>
              )}
              {card.structure && (
                <details>
                  <summary>全部结构字段</summary>
                  <ProcessContent value={card.structure} />
                </details>
              )}
              <ProcessJSON title="原始请求与回答 JSON" value={card.events} />
            </li>
          );
        })}
      </ol>
    </section>
  );
}
