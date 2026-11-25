import copy
import json

import pytest
from pydantic import ValidationError, create_model

from drugpaper.facts.figure_models import VisualExtraction
from drugpaper.skills.execution import PackageOutput
from drugpaper.skills.loader import load_package
from drugpaper.skills.output_integrity import reject_empty_replacement, validate_semantic_output
from tests.fakes.mock_http import MockChatServer
from tests.unit.figure.test_scheme_series import ROOT, example
from tests.unit.test_openai_compat import completion, make_client, req


async def test_status_only_error_preserves_entire_extraction_without_model_rewrite():
    payload = example()
    payload["status"] = "ok"
    original = copy.deepcopy(payload)
    wire = create_model("ConservativeSchemeWire", __base__=PackageOutput)
    wire.package_schema = load_package(ROOT).output_schema
    wire.semantic_model = VisualExtraction
    server = MockChatServer(completion(json.dumps(payload)))
    response = await make_client(server).chat(req(response_schema=wire))
    result = response.parsed.model_dump()
    assert server.call_count == 1
    assert result["status"] == "unclear"
    assert result.pop("validation_adjustments")[0]["field_path"] == "/status"
    result["status"] = "ok"
    assert result == original
    assert payload == original


def test_status_normalization_does_not_hide_other_structure_errors():
    payload = example()
    payload["status"] = "ok"
    payload["pathways"][0]["steps"][0]["reactants"][0]["compound_series"]["member_count"] = 999
    with pytest.raises(ValidationError):
        validate_semantic_output(payload, VisualExtraction)


def test_valid_ok_status_is_not_downgraded():
    payload = {"status": "ok", "description": "No unresolved members"}
    assert validate_semantic_output(payload, VisualExtraction) == payload


@pytest.mark.parametrize("field", ["pathways", "optimization_steps", "rows", "compound_instances"])
def test_empty_replacement_is_rejected(field):
    with pytest.raises(ValueError, match="extraction_content_lost"):
        reject_empty_replacement({field: [{"evidence_text": "existing"}]}, {field: []})


def test_first_unreadable_result_and_nonempty_replacement_are_allowed():
    reject_empty_replacement({}, {"pathways": []})
    reject_empty_replacement({"pathways": [{}]}, {"pathways": [{"steps": []}]})
