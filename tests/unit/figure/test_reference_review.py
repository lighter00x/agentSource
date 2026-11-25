import base64
from types import SimpleNamespace

import pytest

from drugpaper.agents.figure.reference_review import review_reference_assignments
from drugpaper.agents.figure.workflow import EditRejected
from drugpaper.gateway.base import ImagePart
from drugpaper.storage.localfs import LocalFsBlobStore


async def test_source_review_sees_actual_selected_pixels_and_cached_failure_cannot_bypass(tmp_path):
    store = LocalFsBlobStore(tmp_path)
    blob = await store.put(b"source-pixels", "image/png")
    state = {
        "user_id": "default",
        "exploration": {
            "active_call_id": "c1",
            "evidence": [
                {"id": "proof", "result": {"node_ref": "d/r1/p009.g000", "image_refs": [{"blob_ref": blob}]}}
            ],
        },
    }
    target = ImagePart(data_b64=base64.b64encode(b"target-pixels").decode(), mime="image/png")
    events, calls = [], []

    async def ctx(s):
        return None, SimpleNamespace(materials=SimpleNamespace(images=[target], target={}))

    async def run(s, name, inputs, **kwargs):
        calls.append(name)
        assert inputs["sources"][0]["image_indices"] == [1]
        assert base64.b64decode(kwargs["images"][1].data_b64) == b"source-pixels"
        return {
            "status": "needs_revision",
            "summary": "Source field differs",
            "issues": [
                {
                    "assignment_index": 0,
                    "field": "substituents",
                    "description": "Different alkyl group",
                    "source_evidence": "Source row explicitly identifies the shorter group",
                    "suggested_action": "Correct this one substituent field",
                }
            ],
        }

    async def emit(s, event):
        events.append(event)

    agent = SimpleNamespace(
        _ctx=ctx,
        _run_skill=run,
        _exploration_event=emit,
        services=SimpleNamespace(
            store_for=lambda user: store, settings=SimpleNamespace(figure_exploration_max_context_images=8)
        ),
    )
    args = {"evidence_ids": ["proof"], "assignments": [{"reference": "lead-Q"}]}
    for _ in range(2):
        with pytest.raises(EditRejected) as failure:
            await review_reference_assignments(agent, state, args)
        assert failure.value.details["code"] == "reference_assignment_review_failed"
    assert calls == ["reference_assignment_audit"]
    assert events[0]["parent_call_id"] == "c1" and len(events[0]["image_refs"]) == 2
