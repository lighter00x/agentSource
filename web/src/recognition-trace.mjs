const object = (v) =>
  v && typeof v === "object" && !Array.isArray(v) ? v : {};
const array = (v) => (Array.isArray(v) ? v : []);
const imageRef = (v) => (typeof v === "string" ? v : object(v).blob_ref);
export function recognitionTrace(
  events,
  detectionEvents = [],
  structures = [],
) {
  const boxes = detectionEvents
    .filter(
      (e) =>
        e.kind === "skill_result" && object(e.data).name === "molecule_detect",
    )
    .flatMap((e) => array(object(object(e.data).payload).boxes));
  const detection = detectionEvents.findLast(
    (e) =>
      e.kind === "skill_result" && object(e.data).name === "molecule_detect",
  );
  const originalImages = array(
    object(object(detection?.data).payload).image_refs,
  ).map(imageRef);
  const boxFor = (ref) =>
    boxes.find(
      (b) =>
        b.node_ref === ref ||
        (b.local_id && String(ref).split("/").at(-1) === b.local_id),
    );
  const groups = [];
  let current = null;
  for (const e of events) {
    const d = object(e.data);
    if (e.kind === "skill_start" && d.name === "molecule_recognize") {
      current = { start: e, end: null, events: [] };
      groups.push(current);
    } else if (current) {
      current.events.push(e);
      if (e.kind === "skill_result" && d.name === "molecule_recognize") {
        current.end = e;
        current = null;
      }
    } else if (e.kind === "skill_result" && d.name === "molecule_recognize")
      groups.push({ start: null, end: e, events: [] });
  }
  const cards = [];
  for (const group of groups) {
    const input = object(group.start?.data),
      output = object(group.end?.data);
    const returned = array(object(output.payload).structures);
    const tools = group.events.filter(
      (e) =>
        e.kind === "tool_request" &&
        object(e.data).name === "molparser.recognize",
    );
    const responses = group.events.filter(
      (e) =>
        ["tool_response", "tool_error"].includes(e.kind) &&
        object(e.data).name === "molparser.recognize",
    );
    const crops = tools.flatMap((e) =>
      array(object(object(e.data).arguments).image_refs).map(imageRef),
    );
    const records = [];
    for (let i = 0; i < tools.length; i++) {
      const count = array(
        object(object(tools[i].data).arguments).image_refs,
      ).length;
      const response = object(responses[i]?.data);
      const result = array(object(response.result).results);
      for (let j = 0; j < count; j++)
        records.push(
          result.find((r) => r.input_index === j) || { error: response.error },
        );
    }
    const count = Math.max(returned.length, crops.length, input.target ? 1 : 0);
    for (let i = 0; i < count; i++) {
      const structure = object(returned[i]);
      const ref = structure.node_ref || input.target;
      const box = object(boxFor(ref)),
        molecule = object(input.molecule);
      const cached = object(output.trace).cached === true;
      cards.push({
        node_ref: ref,
        mol_id: molecule.mol_id ?? box.reading_index,
        bbox: molecule.local_bbox ?? box.local_bbox,
        segment: molecule.parent_region_index ?? box.parent_region_index,
        crop_ref: crops[i],
        structure: returned[i],
        raw_result:
          records[i] ||
          (responses.some((e) => e.kind === "tool_error")
            ? {
                error: object(
                  responses.find((e) => e.kind === "tool_error").data,
                ).error,
              }
            : undefined),
        complete: !!group.end,
        source: cached ? "cache" : "recognizer",
        manual_override: false,
        events: [group.start, ...group.events].filter(Boolean),
      });
    }
  }
  for (const e of events.filter((e) => e.kind === "manual_reuse")) {
    const d = object(e.data);
    if (!d.node_ref) continue;
    const attempted = cards.find((card) => card.node_ref === d.node_ref);
    if (attempted) {
      attempted.manual_override = true;
      attempted.events.push(e);
      continue;
    }
    const box = object(boxFor(d.node_ref));
    cards.push({
      node_ref: d.node_ref,
      mol_id: d.mol_id ?? box.reading_index,
      bbox: box.local_bbox,
      segment: box.parent_region_index,
      structure: d.structure,
      source: "manual",
      complete: true,
      events: [e],
    });
  }
  for (const box of boxes) {
    if (
      cards.some(
        (c) =>
          c.mol_id === box.reading_index ||
          (c.node_ref && String(c.node_ref).split("/").at(-1) === box.local_id),
      )
    )
      continue;
    const structure = array(structures).find(
      (s) =>
        s.node_ref === box.node_ref ||
        (s.node_ref && String(s.node_ref).split("/").at(-1) === box.local_id),
    );
    cards.push({
      node_ref: structure?.node_ref || box.node_ref || box.local_id,
      mol_id: box.reading_index,
      bbox: box.local_bbox,
      segment: box.parent_region_index,
      structure,
      source: structure?.source === "manual" ? "manual" : "unrecorded",
      complete: false,
      events: [],
    });
  }
  return cards
    .map((card) => ({
      ...card,
      source_image_ref: originalImages[card.segment ?? 0],
    }))
    .sort((a, b) =>
      typeof a.mol_id === "number" && typeof b.mol_id === "number"
        ? a.mol_id - b.mol_id
        : 0,
    );
}
