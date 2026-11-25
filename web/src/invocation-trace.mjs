// Preserve start order and pair by explicit ID when available; otherwise use FIFO
// within a call kind/name. Transport retries belong to their model invocation.
export function invocationTrace(events) {
  const rows = [],
    pending = [];
  let round = 0;
  for (const [sequence, event] of events.entries()) {
    const d = event.data || {};
    const kind = event.kind;
    const type = kind.startsWith("model_")
      ? "model"
      : kind.startsWith("skill_")
        ? "skill"
        : kind.startsWith("tool_")
          ? "tool"
          : null;
    if (kind.startsWith("transport_")) {
      const model = pending.findLast((r) => r.type === "model");
      if (model) model.transport.push(event);
      else
        rows.push({
          type: "record",
          name: kind,
          sequence,
          round,
          start: event,
          transport: [],
        });
      continue;
    }
    if (
      type &&
      ["model_request", "skill_start", "tool_request"].includes(kind)
    ) {
      if (type === "model") round++;
      const row = {
        type,
        name: d.name || d.task || d.model || type,
        sequence,
        round,
        start: event,
        result: null,
        transport: [],
      };
      rows.push(row);
      pending.push(row);
      continue;
    }
    if (
      type &&
      [
        "model_response",
        "model_error",
        "skill_result",
        "tool_response",
        "tool_error",
      ].includes(kind)
    ) {
      const id = d.call_id || d.id;
      const index = pending.findIndex(
        (r) =>
          r.type === type &&
          (!d.name || r.name === d.name) &&
          (!id ||
            !(r.start.data.call_id || r.start.data.id) ||
            id === (r.start.data.call_id || r.start.data.id)),
      );
      if (index >= 0) pending.splice(index, 1)[0].result = event;
      else
        rows.push({
          type,
          name: d.name || d.model || type,
          sequence,
          round,
          start: null,
          result: event,
          transport: [],
        });
      continue;
    }
    if (!["agent_exploration", "stage_start", "stage_result"].includes(kind))
      rows.push({
        type: "record",
        name: kind,
        sequence,
        round,
        start: event,
        transport: [],
      });
  }
  return rows;
}
