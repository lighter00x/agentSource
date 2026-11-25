from __future__ import annotations

import httpx2
import pytest

from drugpaper.chem.formula import check_hrms, composition, parse_hrms
from drugpaper.chem.opsin import OpsinClient


@pytest.mark.parametrize(
    "reported,adduct,basis",
    [
        ("C2H6O", "", "neutral"),
        ("H6C2O", "[M+H]+", "neutral"),
        ("C2H7O", "[M+H]+", "adduct"),
        ("C2H6ONa", "[M+Na]+", "adduct"),
        ("C2H5O", "[M-H]-", "adduct"),
        ("C2H10NO", "[M+NH4]+", "adduct"),
    ],
)
def test_formula_neutral_or_explicit_adduct(reported, adduct, basis):
    result = check_hrms("C2H6O", {"formula": reported, "adduct": adduct})
    assert result["formula_match"] is True and result["formula_basis"] == basis


def test_formula_missing_unsupported_and_mismatch_are_distinct():
    assert check_hrms("C2H6O", None)["formula_match"] is None
    assert check_hrms("C2H6O", {"formula": "C2H6O", "adduct": "[2M+H]+"})["formula_match"] is None
    assert check_hrms("C2H6O", {"formula": "C2H5O", "adduct": ""})["formula_match"] is False
    assert check_hrms("C2H6O", {"formula": "C2H6O.2H2O", "adduct": ""})["formula_match"] is None
    assert parse_hrms("HRMS calculated for C2H7O [M+H]+ 47.0") == {"formula": "C2H7O", "adduct": "[M+H]+"}
    with pytest.raises(ValueError):
        composition("C0H6O")


async def test_opsin_unconfigured_never_sends_request():
    def handler(request):
        raise AssertionError("network must not be used")

    client = OpsinClient(http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    assert (await client.parse("ethanol")).status == "unavailable"
    assert (await client.parse("")).status == "failure"
    await client.aclose()


@pytest.mark.parametrize("status", ["SUCCESS", "WARNING", "FAILURE"])
async def test_opsin_protocol_preserves_smiles_and_warnings(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(
            404 if status == "FAILURE" else 200,
            json={
                "status": status,
                "smiles": " CCO ",
                "warnings": ["stereo ignored"],
                "message": "detail",
            },
        )

    client = OpsinClient(
        "http://mock/opsin", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    result = await client.parse("2/3-(test)? #name")
    assert result.status == status.lower()
    assert result.smiles == (" CCO " if status != "FAILURE" else None)
    assert result.warnings == ["stereo ignored"]
    assert calls[0].url.query == b"" and b"%2F" in calls[0].url.raw_path and b"%23" in calls[0].url.raw_path
    await client.aclose()


@pytest.mark.parametrize(
    "body,http_status", [({}, 200), (["CCO"], 200), ({"status": "SUCCESS"}, 200), ({}, 503)]
)
async def test_opsin_malformed_or_unavailable_response_is_retryable(body, http_status):
    client = OpsinClient(
        "http://mock/opsin",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda req: httpx2.Response(http_status, json=body))
        ),
    )
    assert (await client.parse("ethanol")).status == "unavailable"
    await client.aclose()
