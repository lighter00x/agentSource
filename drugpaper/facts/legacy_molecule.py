"""旧 schema handler 的轻量适配对象；正式状态由节点/候选保存。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MoleculeType(StrEnum):
    UNKNOWN = "unknown"
    FULL = "full"
    SCAFFOLD = "scaffold"
    SUBSTITUENT = "substituent"
    COMPONENT = "component"
    SRU = "sru"


@dataclass
class Molecule:
    id: str
    esmiles: str
    bbox: list[float] | None = None
    molecule_type: str = "unknown"
    molecule_type_source: str = "esmiles"

    def set_molecule_type(self, role: MoleculeType, source: str) -> bool:
        changed = self.molecule_type != role.value
        self.molecule_type, self.molecule_type_source = role.value, source
        return changed
