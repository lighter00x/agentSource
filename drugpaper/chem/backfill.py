"""参考旧流水线 mol_id 回填，返回新对象；严格拒绝不存在/布尔型编号。"""

from __future__ import annotations

import copy
from typing import Any


def backfill(data: dict[str, Any], molecules: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = copy.deepcopy(data)
    errors: list[dict[str, Any]] = []

    def lookup(value: Any, path: str) -> str | None:
        if type(value) is not int or value not in molecules:
            errors.append({"code": "E_UNKNOWN_MOL_ID", "path": path, "mol_id": value})
            return None
        return molecules[value]

    def walk(value: Any, path: str) -> None:
        if isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")
        elif isinstance(value, dict):
            if "mol_id" in value:
                value["esmiles"] = lookup(value["mol_id"], path + ".mol_id")
            if "mol_ids" in value:
                ids = value["mol_ids"]
                if not isinstance(ids, list):
                    errors.append({"code": "E_MOL_IDS_TYPE", "path": path})
                else:
                    value["esmiles_list"] = [lookup(i, path + ".mol_ids") for i in ids]
            for key, child in list(value.items()):
                if key not in {"esmiles", "esmiles_list"}:
                    walk(child, f"{path}.{key}")

    walk(result, "$")
    return result, errors
