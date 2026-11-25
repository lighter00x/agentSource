import base64
import io
import json

from PIL import Image

from drugpaper.facts.figure_models import Cell, TableGrid
from drugpaper.gateway.base import ChatResponse, ImagePart
from drugpaper.skills.figure import table_extract
from drugpaper.skills.materials import Materials
from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller


async def test_large_table_slicing_keeps_original_reference_in_same_call():
    stream = io.BytesIO()
    Image.new("RGB", (200, 3600), "white").save(stream, format="PNG")
    part = ImagePart(data_b64=base64.b64encode(stream.getvalue()).decode(), mime="image/png")

    class Fake:
        def __init__(self):
            self.requests = []

        async def chat(self, req, *, task="default"):
            self.requests.append(req)
            assert len([p for p in req.messages[1].content if isinstance(p, ImagePart)]) == 2
            i = len(self.requests)
            grid = TableGrid(
                orientation="compounds_as_rows",
                header_rows=[[Cell(text="Compound"), Cell(text="EC50 (μM)")]],
                rows=[[Cell(text=str(i)), Cell(text="1")]],
            )
            return ChatResponse(content=json.dumps(grid.model_dump()))

    fake = Fake()
    ctx = SkillContext(
        user_id="default",
        document_id="doc",
        revision=1,
        target_ref="doc/r1/p000.g000",
        run_id="r",
        trace_id="t",
        model=fake,
        tools=ToolCaller(frozenset(), {}),
        blobs=None,
        budget=SkillBudget(12, 900),
        materials=Materials(target={"min_text_height_px": 16}, images=[part]),
        extras={"reference_images": [part]},
    )
    result = await table_extract({}, ctx)
    payload = result.artifacts[0].payload
    assert payload["slice_calls"] == 3 and len(fake.requests) == 3
    assert len(payload["rows"]) == 3
