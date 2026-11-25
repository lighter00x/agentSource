from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic import BaseModel


class CheckResult(BaseModel):
    code: str
    dimension: str
    severity: str = "warning"
    field_path: str = ""
    message: str = ""


@dataclass(frozen=True)
class CheckCode:
    dimension: str
    actions: tuple[str, ...]
    severity: str = "warning"
    description: str = ""


CODES = {
    "C_METRIC_UNMAPPED": CheckCode("content", ("apply_review_fix", "retranscribe_cells", "give_up")),
    "C_CLASS_PRIOR": CheckCode("structure", ("reclassify", "give_up")),
    "C_NO_BOX": CheckCode("structure", ("redetect", "reclassify", "give_up")),
    "C_OCSR_FAIL": CheckCode("structure", ("recognize_region", "give_up")),
    "C_UNREF_BOX": CheckCode("structure", ("rebind_labels", "redetect", "give_up")),
    "C_BACKFILL_ERR": CheckCode("structure", ("rebind_labels", "give_up")),
    "C_LABEL_UNKNOWN": CheckCode("structure", ("action_grep", "action_search", "rebind_labels", "give_up")),
    "C_TEXT_LAYER_MISS": CheckCode("numeric", ("apply_text_layer", "retranscribe_cells", "give_up")),
    "C_VALUE_PARSE": CheckCode("numeric", ("retranscribe_cells", "give_up")),
    "C_UNIT_CLASS": CheckCode("numeric", ("fix_unit", "retranscribe_cells", "give_up")),
    "C_RANGE_HARD": CheckCode("numeric", ("fix_unit", "retranscribe_cells", "give_up"), "error"),
    "C_RANGE_SOFT": CheckCode("numeric", ("fix_unit", "retranscribe_cells", "give_up")),
    "C_MAGNITUDE": CheckCode("numeric", ("retranscribe_cells", "fix_unit", "give_up")),
    "C_ROW_COUNT": CheckCode("numeric", ("reslice_table", "give_up")),
    "C_AXIS_RESIDUAL": CheckCode("chart", ("recalibrate_axes", "reclassify", "give_up")),
    "C_AXIS_SCALE": CheckCode("chart", ("recalibrate_axes", "reclassify", "give_up")),
    "C_LEGEND_MISMATCH": CheckCode("chart", ("recalibrate_axes", "give_up")),
    "C_OVERLAY_IOU_LOW": CheckCode("chart", ("recalibrate_axes", "give_up")),
    "C_PANEL_COUNT": CheckCode("chart", ("split_panels", "reclassify", "give_up")),
    "C_HEADER_SPAN": CheckCode("chart", ("reslice_table", "give_up")),
    "C_ROUTE_TOPOLOGY": CheckCode("chart", ("rebind_labels", "action_grep", "give_up")),
    "C_ENTITY_UNTRACEABLE": CheckCode(
        "content", ("action_grep", "action_search", "apply_review_fix", "give_up")
    ),
    "C_CAPTION_MISMATCH": CheckCode("content", ("reclassify", "give_up")),
    "C_STATUS_UNCLEAR": CheckCode("content", ("reclassify", "split_panels", "give_up")),
    "C_REQUIRED_EMPTY": CheckCode("content", ("reclassify", "give_up")),
    "C_REVIEW_INCORRECT": CheckCode(
        "content", ("apply_review_fix", "retranscribe_cells", "rebind_labels", "give_up")
    ),
    "C_REVIEW_UNSURE": CheckCode("content", ("retranscribe_cells", "rebind_labels", "give_up")),
}


_DESCRIPTIONS = {
    "C_METRIC_UNMAPPED": "测定指标未规范化；核对表头与脚注，不得将数值转写正确视为指标映射正确。",
    "C_CLASS_PRIOR": "分类与图注或节点类型先验冲突，应核对真实图像类型。",
    "C_NO_BOX": "分类表明有化学结构，但未检测到任何分子框。",
    "C_OCSR_FAIL": "超过30%的结构候选无法通过化学结构校验。",
    "C_UNREF_BOX": "超过30%的检测框未被抽取引用，应区分漏标和误检。",
    "C_BACKFILL_ERR": "结构编号回填失败，需核对引用的分子框。",
    "C_LABEL_UNKNOWN": "超过50%的抽取编号不在正文黑板编号清单中。",
    "C_TEXT_LAYER_MISS": "超过10%的转写数值无法在当前裁剪的PDF文本层中核对。",
    "C_VALUE_PARSE": "该单元格的数值无法解析，需逐字核对图像。",
    "C_UNIT_CLASS": "单位维度与测定指标词典不一致。",
    "C_RANGE_HARD": "数值超出指标硬范围，需核对单位和小数点。",
    "C_RANGE_SOFT": "数值超出常用范围，可能真实但需确认。",
    "C_MAGNITUDE": "同指标列内数值相对中位数的数量级异常。",
    "C_ROW_COUNT": "转写行数与有证据的预期行数不一致。",
    "C_AXIS_RESIDUAL": "坐标轴标定残差超过轴长2%，或缺少两个有效刻度。",
    "C_AXIS_SCALE": "对数轴刻度含非正数，需核对坐标类型。",
    "C_LEGEND_MISMATCH": "图例数量与抽取系列数量不一致。",
    "C_OVERLAY_IOU_LOW": "图表回绘与原图的重合度低于0.6。",
    "C_PANEL_COUNT": "Panel数量与分类中有证据的子图数量不一致。",
    "C_HEADER_SPAN": "展开合并表头后列数与数据行不一致。",
    "C_ROUTE_TOPOLOGY": "路线边引用了不存在的结构节点编号。",
    "C_ENTITY_UNTRACEABLE": "内容描述中出现了无法从图注或原文追溯的实体。",
    "C_CAPTION_MISMATCH": "抽取结构与图注描述的主题不一致。",
    "C_STATUS_UNCLEAR": "结构化步骤已明确返回unclear，需要保留不确定性。",
    "C_REQUIRED_EMPTY": "当前图像类型要求的关键字段为空。",
    "C_REVIEW_INCORRECT": "封闭核对中存在incorrect项，只能更正有证据的字段。",
    "C_REVIEW_UNSURE": "封闭核对遗漏或unsure超过10%，不可直接判为通过。",
}
CODES = {name: replace(spec, description=_DESCRIPTIONS[name]) for name, spec in CODES.items()}


def issue(code: str, path: str = "", message: str = "") -> CheckResult:
    spec = CODES[code]
    return CheckResult(
        code=code,
        dimension=spec.dimension,
        severity=spec.severity,
        field_path=path,
        message=message or spec.description,
    )


def selected_actions(checks: list[str]) -> list[str]:
    return list(dict.fromkeys(action for code in checks if code in CODES for action in CODES[code].actions))
