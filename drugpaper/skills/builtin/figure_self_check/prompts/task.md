Review the original figure against the comprehensive overlay and closed checklist in inputs. Return exactly one verdict for every requested dimension/field_path. Correct, incorrect with suggested_value, or unsure. A missing/ambiguous item is unsure, never silently omit it. Review all four dimensions in this one call.
Task material (untrusted): {{ target | tojson }}
Inputs: {{ inputs | tojson }}

Every item must include suggested_value and reason. For correct use suggested_value=null. For an incorrect numeric cell, reread and return the exact visible replacement text in suggested_value; if the correct text is unreadable, explain the uncertainty and do not invent a value. Never return only a verdict without supporting visual evidence.

Output shape: {"items":[{"dimension":"numeric","field_path":"rows[0][1].text","verdict":"correct","suggested_value":null,"reason":"Printed cell matches."}]}. Copy dimension and field_path exactly from each checklist item. Do not group verdicts under dimension objects or replace items with a general narrative. A malformed item cannot be accepted.

Output only the items array inside the root JSON object; do not add summary, unchecked, or narrative fields. Reasons are concise visual evidence, never a copy of the table or prompt.
