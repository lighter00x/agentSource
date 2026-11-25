from typing import Any

from drugpaper.validators.figure import chart, content, numeric, structure
from drugpaper.validators.figure.codes import CheckResult


def check_all(ctx: dict[str, Any]) -> list[CheckResult]:
    return [item for module in (structure, numeric, chart, content) for item in module.check(ctx)]
