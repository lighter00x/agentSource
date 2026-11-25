from typing import Any, TypedDict


class FigureAgentState(TypedDict, total=False):
    document_id: str
    node_ref: str
    user_id: str
    mode: str
    run_id: str
    trace_id: str
    blackboard: dict[str, Any]
    classification: dict[str, Any]
    chain: list[str]
    exploration: dict[str, Any]
    extraction: dict[str, Any]
    resume_structure: bool
    pre_backfill_extraction: dict[str, Any]
    backfilled_extraction: dict[str, Any]
    molecular_coverage: dict[str, Any]
    image_products: list[dict[str, Any]]
    material: dict[str, Any]
    checks: list[dict[str, Any]]
    review: dict[str, Any]
    checklist: list[dict[str, Any]]
    unchecked: list[dict[str, Any]]
    accounting: dict[str, Any]
    artifact_ids: list[str]
    trace: list[dict[str, Any]]
    round: int
    remediation_started: float
    action: dict[str, Any]
    force: bool
    questions: list[str]
    child_reports: list[dict[str, Any]]
    query_answers: list[dict[str, Any]]
    report: dict[str, Any]
    summary_result: dict[str, Any]
    gave_up: bool
