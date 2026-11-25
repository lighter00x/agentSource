"""E-SMILES 分子拼接（Markush 替换）自包含库。

核心拼接只依赖 ``rdkit``。图片识别相关入口会延迟复用
``molparser.client.MolParserClient``，默认连接 ``http://localhost:8001``。

核心功能
--------
``substitute_markush(caption, definitions)`` 把一条 E-SMILES（含 R 基团占位符）
按 ``definitions`` 字典中的真实基团定义展开，生成具体的 SMILES（或 E-SMILES）。

``recognize_and_substitute(image, definitions)`` 先调用 MolParser 服务识别骨架图，
再进行替换；``substitute_markush_images(scaffold_image, definition_images)`` 还可
把取代基图片一并识别成 SMILES 后完成端到端拼接。

E-SMILES 是 SMILES 与 Markush 标注的组合：``SMILES<sep>标注``。标注里：

- ``<a>idx:label</a>``  原子索引组：在编号 ``idx`` 的原子上挂基团
- ``<r>idx:label</r>``  环索引组：在编号 ``idx`` 的环上枚举挂位
- ``?n``                多重性：数字(精确)、``n``(1..环位)、``n-m``(范围)
- ``*``                 定义片段中的星号 = 连接点，拼接时自动并入并移除

definitions 键支持 ``R1`` 与 ``R[1]`` 两种写法（自动归一化），
值为 SMILES 片段或缩写名（``Me``/``CF3``/``Boc`` 等，见文件内 ``_ABBREV_SMI``）。

示例::

    from molecule_splicer import substitute_markush

    caption = "COC(*NC1C(OC)=CC=C(CS(=O)(=O)C2=CC=CC=C2)C=1)=O<sep><r>1:R[1]</r><a>3:R[2]</a>"
    result = substitute_markush(
        caption,
        {"R1": "C(F)(F)F", "R2": "OCC"},          # 真实基团（SMILES 片段）
    )
    # => 生成 R1 -> CF3、R2 -> OCH2CH3 的具体分子 SMILES

本文件提取自 MolParser 项目 ``molparser/esmiles_lib.py``，对应 Apache-2.0
许可证见同目录 ``LICENSE.MolParser``。当前项目仅做独立能力归档，尚未接入论文流水线。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from rdkit import Chem, RDLogger

logger = logging.getLogger(__name__)

__all__ = [
    "AtomIndex",
    "GroupDesc",
    "Index",
    "Patterns",
    "PERIODIC_TABLE",
    "RingIndex",
    "RecognitionSubstitutionResult",
    "TextType",
    "Tokens",
    "TranslatedMolecule",
    "Translator",
    "build_esmi",
    "get_abbrev_smi",
    "parse_caption",
    "recognize_and_substitute",
    "recognize_esmiles",
    "substitute_markush_images",
    "substitute_markush",
]


# ===========================================================================
# 缩写表（原 abbrevs_example.csv 内嵌）
# ===========================================================================
_ABBREV_SMI: Dict[str, str] = {
    "Me": "C",
    "Boc": "C(=O)OC(C)(C)C",
    "NO2": "[N+](=O)[O-]",
    "THP": "C1CCCCO1",
    "COOH": "C(=O)[OH]",
    "OBn": "OCc1ccccc1",
    "CN": "C#N",
    "Ph": "c1ccccc1",
    "CF3": "C(F)(F)F",
    "Bz": "C(=O)c1ccccc1",
    "N3": "N=[N+]=[N-]",
    "Ac": "C(=O)C",
    "CHO": "C=O",
    "Bn": "Cc1ccccc1",
    "PMB": "Cc1ccc(OC)cc1",
    "CO2H": "C(=O)[OH]",
    "SEM": "COCC[Si](C)(C)C",
    "NHBoc": "NC(=O)OC(C)(C)C",
    "COOMe": "C(=O)OC",
    "Bpin": "B1OC(C)(C)C(C)(C)O1",
    "Cbz": "C(=O)OCc1ccccc1",
    "D": "[2H]",
    "OMe": "OC",
    "CONHtBu": "C(=O)NC(C)(C)C",
    "OBz": "OC(=O)c1ccccc1",
    "NHAc": "NC(=O)C",
    "OCH3": "OC",
    "CH3": "C",
    "TBS": "[Si](C)(C)C(C)(C)C",
    "i-Pr": "C(C)C",
    "Et": "CC",
    "CD3": "C([2H])([2H])[2H]",
    "TBDPS": "[Si](c1ccccc1)(c1ccccc1)C(C)(C)C",
    "COOEt": "C(=O)OCC",
    "OAc": "OC(=O)C",
    "OEt": "OCC",
    "SO2Me": "S(=O)(=O)C",
    "tBu": "C(C)(C)C",
    "NHOH": "NO",
    "TFA": "C(=O)C(F)(F)F",
    "TIPS": "[Si](C(C)C)(C(C)C)C(C)C",
    "Ts": "S(=O)(=O)c1ccc(C)cc1",
    "Cy": "C1CCCCC1",
    "DMB": "Cc1ccc(C)c(C)c1",
    "Mn": "[Mg]",
    "Ms": "S(C)(=O)=O",
    "OTf": "OS(=O)(=O)C(F)(F)F",
    "c-Pr": "C1CC1",
    "iPr": "C(C)C",
    "CO2Et": "C(=O)OCC",
    "CO2Me": "C(=O)OC",
    "CoA": "CCNC(=O)CCNC(=O)C(O)C(C)(C)COP(=O)([O-])OP(=O)([O-])OCC1OC(n2cnc3c(N)ncnc32)C(O)C1OP(=O)([O-])[O-]",
    "Cp": "C1=CC=CC1",
    "MEM": "COCCOC",
    "Ns": "S(=O)(=O)c1ccc([N+](=O)[O-])cc1",
    "OMEM": "OCOCCOC",
    "SO2NH2": "S(N)(=O)=O",
    "SO3H": "S(=O)(=O)[OH]",
    "Tr": "C(c1ccccc1)(c1ccccc1)c1ccccc1",
    "Trt": "C(c1ccccc1)(c1ccccc1)c1ccccc1",
    "BzO": "OC(=O)c1ccccc1",
    "C11H23": "CCCCCCCCCCC",
    "C12H25": "CCCCCCCCCCCC",
    "C14H29": "CCCCCCCCCCCCCC",
    "C16H33": "CCCCCCCCCCCCCCCC",
    "C18H37": "CCCCCCCCCCCCCCCCCC",
    "C20H41": "CCCCCCCCCCCCCCCCCCCC",
    "COO-": "C(=O)[O-]",
    "H": "[H]",
    "NHAlloc": "NC(=O)OCC=C",
    "NHTFA": "NC(=O)C(F)(F)F",
    "OtBu": "OC(C)(C)C",
    "Py": "c1ccccn1",
    "STol": "Sc1ccc(C)cc1",
    "TBDMS": "[Si](C)(C)C(C)(C)C",
    "TBSO": "O[Si](C)(C)C(C)(C)C",
    "Tf": "S(=O)(=O)C(F)(F)F",
    "t-Bu": "C(C)(C)C",
    "Ad": "C12C[C@H]3C[C@@H](C1)C[C@@H](C2)C3",
    "Alloc": "C(=O)OCC=C",
    "BPin": "B1OC(C)(C)C(C)(C)O1",
    "BnO": "OCc1ccccc1",
    "Bu": "CCCC",
    "C7H15": "CCCCCCC",
    "CF2": "[C](F)F",
    "CONH2": "C(N)=O",
    "LysO": "ON[C@@H](CCCCN)C(*)=O",
    "MgBr": "[Mg]Br",
    "NC": "[N+]#[C-]",
    "NCO": "N=C=O",
    "NHCH3": "NC",
    "NHEt": "NCC",
    "NaO": "O[Na]",
    "OH": "O",
    "OMs": "OS(C)(=O)=O",
    "ONa": "O[Na]",
    "OTBS": "O[Si](C)(C)C(C)(C)C",
    "OTHP": "OC1CCCCO1",
    "OTs": "OS(=O)(=O)c1ccc(C)cc1",
    "Pd": "[Pd]",
    "PhS": "Sc1ccccc1",
    "SCN": "SC#N",
    "SMe": "SC",
    "SO2F": "S(=O)(=O)F",
    "Su": "N1C(=O)CCC1=O",
    "Zn": "[Zn]",
    "boc": "C(=O)OC(C)(C)C",
    "n-Bu": "CCCC",
}


# ===========================================================================
# chem_utils —— RDKit 辅助：缩写解析 / 原子合并 / 基团重映射 / 碳链展开
# ===========================================================================
def get_abbrev_smi() -> Dict[str, str]:
    """内置缩写 → SMILES 映射表（Me/Et/Boc/CF3/Ph 等）。"""
    return _ABBREV_SMI


def get_mol(smi: str) -> Chem.rdchem.Mol:
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smi}")
    return mol


def _preserved_records(groups: str) -> str:
    pattern = r"<s>.*?</s>|<g>.*?</g>|<v>.*?</v>|<r><v>\d+:.+?</r>"
    return "".join(match.group(0) for match in re.finditer(pattern, groups, re.DOTALL))


def _strip_preserved_records(groups: str) -> str:
    pattern = r"<s>.*?</s>|<g>.*?</g>|<v>.*?</v>|<r><v>\d+:.+?</r>"
    return re.sub(pattern, "", groups, flags=re.DOTALL)


def split_groups(groups: str) -> Tuple[Dict[int, Dict[str, str]], Dict[int, str]]:
    """把标注字符串拆成 原子索引组 a_groups 与 环索引组 r_groups。"""
    pattern = r"<(?P<type>r|a|d)>(?P<content>.*?)</(?P=type)>"
    matches = re.finditer(pattern, _strip_preserved_records(groups))
    a_groups: Dict[int, Dict[str, str]] = {}
    r_groups: Dict[int, str] = {}

    for m in matches:
        type_ = m.group("type")
        text = m.group("content")
        if ":" in text:
            ind, content = text.split(":", 1)
        else:
            ind, content = "", text
        if not ind.isdigit():
            continue
        if type_ == "r":
            r_groups[int(ind)] = content
        else:
            a_groups[int(ind)] = {"content": content, "type": type_}
    return a_groups, r_groups


def get_groups_str(a_groups: Dict[int, Dict], r_groups: Dict[int, Dict] | None = None) -> str:
    """把原子/环索引组重新拼成标注字符串。"""
    groups_str = ""
    for k, v in sorted(a_groups.items(), key=lambda x: x[0]):
        tag = v.get("type", "a")
        groups_str += f"<{tag}>{k}:{v['content']}</{tag}>"
    if r_groups is not None:
        for k, v in sorted(r_groups.items(), key=lambda x: x[0]):
            groups_str += f"<r>{k}:{v['content']}</r>"
    return groups_str


def _canonical_output_order(mol: Chem.rdchem.Mol) -> Tuple[Dict[int, int], Chem.rdchem.Mol | None]:
    output_mol = Chem.Mol(mol)
    for atom in output_mol.GetAtoms():
        atom.SetAtomMapNum(0)

    smi = Chem.MolToSmiles(output_mol, canonical=True, isomericSmiles=True)
    final_mol = Chem.MolFromSmiles(smi)
    try:
        import ast

        order = ast.literal_eval(output_mol.GetProp("_smilesAtomOutputOrder"))
    except Exception:
        order = list(range(output_mol.GetNumAtoms()))
    return {internal_idx: output_idx for output_idx, internal_idx in enumerate(order)}, final_mol


def is_single_bond(bond: Chem.rdchem.Bond) -> bool:
    return bond.GetBondType() == Chem.BondType.SINGLE


def alter_atom(
    atom: Chem.rdchem.Atom,
    smiles: None | str,
    element: None | str = None,
) -> None:
    """把原子原位改写为给定 SMILES 的首原子，或给定元素。"""
    if smiles:
        src_mol_check = Chem.MolFromSmiles(smiles)
        rep_atom = src_mol_check.GetAtomWithIdx(0)
        atom.SetAtomicNum(rep_atom.GetAtomicNum())
        atom.SetFormalCharge(rep_atom.GetFormalCharge())
    elif element:
        atom.SetAtomicNum(Chem.GetPeriodicTable().GetAtomicNumber(element))
        atom.SetFormalCharge(0)
    else:
        raise ValueError("Either smiles or element must be provided")

    atom.SetIsotope(0)
    atom.SetNumExplicitHs(0)
    atom.SetNoImplicit(False)
    if atom.GetDegree() == 1:
        atom.SetIsAromatic(False)


def merge_group(tgt_mol: Chem.rdchem.RWMol, src: str, attach_idx: int) -> None:
    """在 * 连接点（attach_idx）处并入 src 片段，移除星号。"""
    dummy_atom = tgt_mol.GetAtomWithIdx(attach_idx)
    assert dummy_atom.GetSymbol() == "*", "Non-dummy attachment point"

    src_mol = get_mol(src)

    neighbors = dummy_atom.GetNeighbors()
    assert len(neighbors) == 1, "Attachment point must have exactly one neighbor"
    nb_idx = neighbors[0].GetIdx()

    conn_bond = tgt_mol.GetBondBetweenAtoms(attach_idx, nb_idx)
    assert conn_bond.GetBondType() == Chem.BondType.SINGLE, (
        "Attachment point must link to single bond"
    )

    idx_map: Dict[int, int] = {}
    for atom in src_mol.GetAtoms():
        i = tgt_mol.AddAtom(atom)
        idx_map[atom.GetIdx()] = i
        tgt_atom = tgt_mol.GetAtomWithIdx(i)
        tgt_atom.SetChiralTag(atom.GetChiralTag())
    for bond in src_mol.GetBonds():
        j = idx_map[bond.GetBeginAtomIdx()]
        k = idx_map[bond.GetEndAtomIdx()]
        tgt_mol.AddBond(j, k, bond.GetBondType())
    tgt_mol.RemoveBond(attach_idx, nb_idx)
    tgt_mol.AddBond(nb_idx, idx_map[0], Chem.BondType.SINGLE)


def remap_groups(mol: Chem.rdchem.Mol, groups: str, ring_info: tuple) -> str:
    """分子改写后按新原子/环编号重映射标注（供保留组输出）。"""
    old_ind2agroup, old_ind2rgroup = split_groups(groups)
    new_ind2agroup: Dict[int, Dict] = {}
    internal_ind_map: Dict[int, int] = {}
    internal_to_output, output_mol = _canonical_output_order(mol)

    for atom in mol.GetAtoms():
        s = atom.GetSmarts()
        atom.SetAtomMapNum(0)
        if ":" not in s:
            continue
        try:
            old_ind = int(s.split(":")[1].replace("]", "")) - 1
        except Exception:
            continue
        internal_ind = atom.GetIdx()
        internal_ind_map[old_ind] = internal_ind
        new_ind = internal_to_output.get(internal_ind, internal_ind)
        group = old_ind2agroup.get(old_ind)
        if group is not None:
            new_ind2agroup[new_ind] = group

    new_ind2rgroup: Dict[int, Dict] = {}
    if old_ind2rgroup:
        new_ring_info = output_mol.GetRingInfo().AtomRings() if output_mol is not None else ()
        new_ring_sets = [set(ring) for ring in new_ring_info]
        ring_map: Dict[int, int | None] = {}

        for orig_idx, orig_ring in enumerate(ring_info):
            mapped = {
                internal_to_output.get(internal_ind_map.get(atom, atom), internal_ind_map.get(atom, atom))
                for atom in orig_ring
            }
            best_match = None
            best_overlap = 0
            for new_idx, new_ring in enumerate(new_ring_sets):
                overlap = len(mapped.intersection(new_ring))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match = new_idx
            ring_map[orig_idx] = best_match if best_overlap > 0 else None

        for old_ind, group in old_ind2rgroup.items():
            new_ring_idx = ring_map.get(old_ind)
            if new_ring_idx is not None:
                new_ind2rgroup[new_ring_idx] = {"content": group, "type": "r"}

    return get_groups_str(new_ind2agroup, new_ind2rgroup) + _preserved_records(groups)


def carbon_chain_repetition_process(mol, atom_id, desc, is_markush, error_msg=False):
    """展开 (CH2)?n 碳链重复。返回是否仍需当作 Markush 保留。"""
    atom = mol.GetAtomWithIdx(atom_id)
    count = 1
    if desc.multiple and desc.multiple.isdigit():
        count = int(desc.multiple)

    try:
        atom.SetAtomicNum(6)
        atom.SetFormalCharge(0)
        atom.SetNumExplicitHs(0)
        atom.SetNoImplicit(False)
        atom.SetIsAromatic(False)
        atom.SetIsotope(0)
        atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)

        for bond in atom.GetBonds():
            bond.SetBondType(Chem.BondType.SINGLE)
            bond.SetIsAromatic(False)

        if count > 1:
            nbrs = atom.GetNeighbors()
            degree = len(nbrs)

            if degree == 2:
                nb_right = nbrs[1]
                right_idx = nb_right.GetIdx()
                mol.RemoveBond(atom_id, right_idx)

                current_end_idx = atom_id
                for _ in range(count - 1):
                    new_atom = Chem.Atom(6)
                    new_atom.SetIsAromatic(False)
                    new_idx = mol.AddAtom(new_atom)
                    mol.AddBond(current_end_idx, new_idx, Chem.BondType.SINGLE)
                    current_end_idx = new_idx
                mol.AddBond(current_end_idx, right_idx, Chem.BondType.SINGLE)

            elif degree == 1:
                current_end_idx = atom_id
                for _ in range(count - 1):
                    new_atom = Chem.Atom(6)
                    new_atom.SetIsAromatic(False)
                    new_idx = mol.AddAtom(new_atom)
                    mol.AddBond(current_end_idx, new_idx, Chem.BondType.SINGLE)
                    current_end_idx = new_idx

            else:
                is_markush = True

    except Exception:
        is_markush = True

    return is_markush


# ===========================================================================
# translator —— E-SMILES 标注解析 / 重排
# ===========================================================================
# 有歧义的元素符号不在此列（如 B/C/N 等以单字符出现）。
PERIODIC_TABLE = {
    "H", "He", "Li", "Be", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Th",
    "Pa", "U",
}


LONG_CHAR_ELEMENTS = (
    "Cl", "Br", "Si", "Na", "Al", "Ca", "Sn", "As", "Hg",
    "Fe", "Zn", "Cr", "Se", "Gd", "Au", "Li",
)


@unique
class TextType(Enum):
    SYMBOL = "symbol"
    SCRIPT = "script"
    MULTIPLE = "multiple"
    PRIME = "prime"


class Index(int):
    def __new__(cls, value: int, **kwds):
        assert isinstance(value, int) and value >= 0
        return super().__new__(cls, value)


class AtomIndex(Index):
    """原子索引组标记：<a>idx:label</a> 中的 idx。"""

    key: int = 1

    def __hash__(self):
        return hash(("atom", int(self)))

    def __eq__(self, other):
        if isinstance(other, AtomIndex):
            return hash(self) == hash(other)
        return False


class RingIndex(Index):
    """环索引组标记：<r>idx:label</r> 中的 idx。"""

    def __init__(self, value: int, virtual: bool = False) -> None:
        self._virtual = virtual
        self._key = 2_000_000 if self._virtual else 1_000_000

    def __hash__(self):
        return hash(("ring", int(self), self._virtual))

    def __eq__(self, other):
        if isinstance(other, RingIndex):
            return hash(self) == hash(other)
        return False

    @property
    def virtual(self) -> bool:
        return self._virtual

    @property
    def key(self) -> bool | int:
        return self._key


class Tokens:
    """标注中使用的特殊 token 名。"""

    atom_start = "<a>"
    atom_end = "</a>"
    circ_start = "<c>"
    circ_end = "</c>"
    dummy_start = "<d>"
    dummy_end = "</d>"
    substruct_start = "<s>"
    substruct_end = "</s>"
    sgroup_start = "<g>"
    sgroup_end = "</g>"
    virtual_start = "<v>"
    virtual_end = "</v>"
    special_id = "<id>"
    ring_start = "<r>"
    ring_end = "</r>"
    dummy = "<dum>"
    separator = "<sep>"


class Patterns:
    """标注解析用的正则。"""

    long_char_elements_pattern = re.compile(
        rf'{"|".join(LONG_CHAR_ELEMENTS) + "|."}'
    )
    grp_content = re.compile(
        rf"(?P<{TextType.SYMBOL.value}>(?:{Tokens.special_id}|[A-Za-z0-9-\(\)]*))"
        + rf"(?P<{TextType.SCRIPT.value}>(\[\S+\])?)"
        + rf"(?P<{TextType.PRIME.value}>[\'\"]?)"
        + rf"(?P<{TextType.MULTIPLE.value}>(\?([a-z]|\d+|\d+-\d+)$)?)"
    )
    grp_pattern = re.compile(
        rf"({Tokens.atom_start}|{Tokens.circ_start}|{Tokens.dummy_start}|{Tokens.ring_start}|{Tokens.ring_start}{Tokens.circ_start}|{Tokens.ring_start}{Tokens.virtual_start})"
        + r"(\d+:\S+?)"
        + rf"({Tokens.atom_end}|{Tokens.circ_end}|{Tokens.dummy_end}|{Tokens.ring_end})"
    )
    trail_pattern = re.compile(
        r"(?P<groups>.*?)(?P<extension>\|Sg:[^|]+\|)?$",
        re.DOTALL,
    )


@dataclass
class GroupDesc:
    """一个 Markush 组的解析结果。"""

    id: Index
    symbol: Optional[str] = None
    script: Optional[str] = None
    prime: Optional[str] = None
    multiple: Optional[str] = None
    is_circle: bool = False
    is_dummy: bool = False

    def __str__(self) -> str:
        if self.is_dummy:
            return "<dum>"
        expr = ""
        if self.is_circle:
            expr = "c"
        if self.symbol is not None:
            expr += self.symbol
        if self.script is not None:
            expr = expr + "[" + self.script + "]"
        if self.prime is not None:
            expr += self.prime
        if self.multiple is not None:
            expr = expr + "?" + self.multiple
        return expr


@dataclass(frozen=True)
class TranslatedMolecule:
    smi: str
    groups: str
    caption: str
    esmi: str
    markush: bool
    sru: bool


class Translator:
    """E-SMILES 标注解析与重排。"""

    @classmethod
    def canonicalize_smiles(cls, smi: str) -> str:
        try:
            mol = Chem.MolFromSmiles(smi)
            if mol is None:
                return smi
            return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
        except Exception:
            return smi

    @classmethod
    def build_esmi(cls, smi: str, groups: str = "", ext: str = "") -> str:
        return f"{smi}{Tokens.separator}{groups}{ext}"

    @classmethod
    def remove_atom_groups(cls, groups: str, atom_indices: set[int]) -> str:
        if not atom_indices:
            return groups
        atom_group_pattern = re.compile(
            rf"(?P<start>{Tokens.atom_start}|{Tokens.dummy_start})"
            r"(?P<idx>\d+):.+?"
            rf"(?P<end>{Tokens.atom_end}|{Tokens.dummy_end})"
        )

        def replace(match: re.Match) -> str:
            return "" if int(match.group("idx")) in atom_indices else match.group(0)

        return atom_group_pattern.sub(replace, groups)

    @classmethod
    def parse_caption(
        cls,
        caption: str,
        return_mol: bool = False,
        error_msg: bool = False,
    ) -> Optional[Tuple[Union[Chem.rdchem.Mol, str], str, str]]:
        """解析 ``SMILES<sep>标注``：返回 (smiles/molecule, groups, extension)。"""
        if Tokens.separator not in caption:
            if error_msg:
                logger.warning(f"No `{Tokens.separator}` found in caption: {caption}")
            return
        smi, trailing = caption.split(Tokens.separator, 1)

        if error_msg:
            RDLogger.EnableLog("rdApp.*")
        mol = Chem.MolFromSmiles(smi)
        RDLogger.DisableLog("rdApp.*")
        if mol is None:
            if error_msg:
                logger.warning(f"Invalid SMILES: {smi}")
            return
        groups, ext = cls.parse_trailing(trailing)
        if return_mol:
            return mol, groups, ext
        return smi, groups, ext

    @classmethod
    def parse_trailing(cls, trailing: str):
        matched = re.match(Patterns.trail_pattern, trailing)
        if matched is None:
            return "", ""
        content = matched.groupdict()
        return content.get("groups") or "", content.get("extension") or ""

    @classmethod
    def parse_extension(cls, ext: str) -> str:
        return ext.strip("|")

    @classmethod
    def parse_groups(cls, seq: str) -> List[GroupDesc]:
        """把标注字符串解析成 GroupDesc 列表。"""
        if seq == "":
            return []
        seq = re.sub(
            rf"{Tokens.substruct_start}.*?{Tokens.substruct_end}|"
            rf"{Tokens.sgroup_start}.*?{Tokens.sgroup_end}|"
            rf"{Tokens.virtual_start}.*?{Tokens.virtual_end}",
            "",
            seq,
            flags=re.DOTALL,
        )
        descriptions: List[GroupDesc] = []
        for grp_start, grp_content, _ in re.findall(Patterns.grp_pattern, seq):
            parsed = cls.parse_group(grp_content)
            if parsed is None:
                continue
            idx, grp_text = parsed
            if grp_start in (Tokens.atom_start, Tokens.dummy_start):
                grp_desc = GroupDesc(id=AtomIndex(idx))
                if len(grp_text) == 0 or grp_start == Tokens.dummy_start:
                    grp_desc.is_dummy = True
            elif grp_start == Tokens.circ_start:
                grp_desc = GroupDesc(id=AtomIndex(idx), is_circle=True)
            elif grp_start in (
                f"{Tokens.ring_start}{Tokens.circ_start}",
                f"{Tokens.ring_start}{Tokens.virtual_start}",
            ):
                grp_desc = GroupDesc(id=RingIndex(idx, virtual=True))
            elif grp_start == Tokens.ring_start:
                grp_desc = GroupDesc(id=RingIndex(idx))
            else:
                continue
            grp_desc.symbol = grp_text.get(TextType.SYMBOL)
            grp_desc.script = grp_text.get(TextType.SCRIPT)
            grp_desc.prime = grp_text.get(TextType.PRIME)
            grp_desc.multiple = grp_text.get(TextType.MULTIPLE)
            descriptions.append(grp_desc)
        return descriptions

    @classmethod
    def parse_group(cls, group: str) -> Optional[Tuple[int, Dict[TextType, str]]]:
        items = group.split(":")
        if len(items) != 2:
            return
        idx, content = items
        if not idx.isdigit():
            return
        idx = int(idx)
        if content == Tokens.dummy:
            return idx, {}
        grp_text = cls.get_group_texts(content)
        if len(grp_text) == 0:
            return
        return idx, grp_text

    @classmethod
    def get_group_texts(cls, content: str) -> Dict[TextType, str]:
        texts: Dict[TextType, str] = {}
        matched = re.match(Patterns.grp_content, content).groupdict()
        for tt_value, text in matched.items():
            if len(text) == 0:
                continue
            if tt_value == TextType.SYMBOL.value:
                texts[TextType.SYMBOL] = text
            elif tt_value == TextType.SCRIPT.value:
                assert text.startswith("[") and text.endswith("]")
                texts[TextType.SCRIPT] = text[1:-1]
            elif tt_value == TextType.PRIME.value:
                texts[TextType.PRIME] = text
            elif tt_value == TextType.MULTIPLE.value:
                assert text.startswith("?")
                texts[TextType.MULTIPLE] = text[1:]
        return texts

    @classmethod
    def repair_atom_group_indices(
        cls,
        mol: Chem.rdchem.Mol,
        trailing: str,
        error_msg: bool = False,
    ) -> str:
        """把缺星号连接的原子索引组修复到最近的 * 原子上。"""
        star_indices = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetSymbol() == "*"]
        if not star_indices or (
            Tokens.atom_start not in trailing and Tokens.dummy_start not in trailing
        ):
            return trailing

        preserved_records: List[str] = []
        preserved_pattern = re.compile(
            rf"{Tokens.substruct_start}.*?{Tokens.substruct_end}|"
            rf"{Tokens.sgroup_start}.*?{Tokens.sgroup_end}|"
            rf"{Tokens.virtual_start}.*?{Tokens.virtual_end}|"
            rf"{Tokens.ring_start}{Tokens.virtual_start}\d+:.+?{Tokens.ring_end}",
            re.DOTALL,
        )

        def hide_preserved(match: re.Match) -> str:
            preserved_records.append(match.group(0))
            return f"@@MOLPARSER_PRESERVED_{len(preserved_records) - 1}@@"

        protected_trailing = preserved_pattern.sub(hide_preserved, trailing)

        atom_group_pattern = re.compile(
            rf"(?P<start>{Tokens.atom_start}|{Tokens.dummy_start})"
            r"(?P<idx>\d+):(?P<content>.+?)"
            rf"(?P<end>{Tokens.atom_end}|{Tokens.dummy_end})"
        )
        matches = list(atom_group_pattern.finditer(protected_trailing))
        if not matches:
            return trailing

        exact_targets = {
            int(match.group("idx"))
            for match in matches
            if int(match.group("idx")) in star_indices
        }
        used_targets: set[int] = set()

        def replace(match: re.Match) -> str:
            raw_idx, content = match.group("idx"), match.group("content")
            idx = int(raw_idx)
            if idx in star_indices and idx not in used_targets:
                used_targets.add(idx)
                return match.group(0)

            candidates = [
                star_idx
                for star_idx in star_indices
                if star_idx not in used_targets and star_idx not in exact_targets
            ]
            if not candidates:
                return match.group(0)

            nearest = min(candidates, key=lambda star_idx: (abs(star_idx - idx), star_idx))
            used_targets.add(nearest)
            if error_msg:
                logger.warning(
                    "Repair atom group index: <a>%s:%s</a> -> <a>%s:%s</a>",
                    raw_idx,
                    content,
                    nearest,
                    content,
                )
            return f"{match.group('start')}{nearest}:{content}{match.group('end')}"

        repaired = atom_group_pattern.sub(replace, protected_trailing)
        for idx, record in enumerate(preserved_records):
            repaired = repaired.replace(f"@@MOLPARSER_PRESERVED_{idx}@@", record)
        return repaired

    @classmethod
    def _protect_stereo_for_dummy_substitution(
        cls,
        smi: str,
        groups: str,
        abbrev_map: Dict[str, str],
        probe: Chem.rdchem.Mol,
        error_msg: bool = False,
    ) -> Tuple[str, set[int]]:
        """临时用同位素区分可替换的 * 原子，保住立体化学解析。"""
        if "@" not in smi or "*" not in smi:
            return smi, set()

        star_indices = [atom.GetIdx() for atom in probe.GetAtoms() if atom.GetSymbol() == "*"]
        if len(star_indices) < 2:
            return smi, set()

        substitutable_indices: set[int] = set()
        for desc in cls.parse_groups(groups):
            if not isinstance(desc.id, AtomIndex) or desc.is_dummy or not desc.symbol:
                continue
            lookup_symbol = desc.symbol + (desc.script or "")
            if (
                desc.symbol == "CH2"
                or (desc.symbol == "CH" and desc.script == "2")
                or lookup_symbol == "CN"
                or lookup_symbol in abbrev_map
            ):
                substitutable_indices.add(int(desc.id))
            elif lookup_symbol in PERIODIC_TABLE and not desc.multiple:
                substitutable_indices.add(int(desc.id))

        if not substitutable_indices.intersection(star_indices):
            return smi, set()

        dummy_tokens: List[Tuple[int, int]] = []
        cursor = 0
        while cursor < len(smi):
            if smi[cursor] == "[":
                closing = smi.find("]", cursor + 1)
                if closing < 0:
                    return smi, set()
                if smi[cursor : closing + 1] == "[*]":
                    dummy_tokens.append((cursor, closing + 1))
                cursor = closing + 1
                continue
            if smi[cursor] == "*":
                dummy_tokens.append((cursor, cursor + 1))
            cursor += 1

        if len(dummy_tokens) != len(star_indices):
            if error_msg:
                logger.warning("Unable to align dummy atoms for stereo preservation")
            return smi, set()

        used_isotopes = {atom.GetIsotope() for atom in probe.GetAtoms()}
        temporary_isotopes: set[int] = set()
        next_isotope = 1
        protected_parts: List[str] = []
        last_end = 0

        for (start, end), atom_idx in zip(dummy_tokens, star_indices):
            protected_parts.append(smi[last_end:start])
            if atom_idx in substitutable_indices:
                while next_isotope in used_isotopes:
                    next_isotope += 1
                protected_parts.append(f"[{next_isotope}*]")
                temporary_isotopes.add(next_isotope)
                used_isotopes.add(next_isotope)
                next_isotope += 1
            else:
                protected_parts.append(smi[start:end])
            last_end = end
        protected_parts.append(smi[last_end:])
        return "".join(protected_parts), temporary_isotopes

    @classmethod
    def refactor(
        cls,
        caption: str,
        error_msg: bool = False,
    ) -> Optional[TranslatedMolecule]:
        """重排 E-SMILES 并判断是否为 Markush / SRU 输出。"""
        if "<sep>" not in caption:
            canonical_smi = cls.canonicalize_smiles(caption)
            return TranslatedMolecule(
                smi=canonical_smi,
                groups="",
                caption=caption,
                esmi=cls.build_esmi(canonical_smi),
                markush=False,
                sru=False,
            )
        smi, trailing = caption.split(Tokens.separator, 1)
        if trailing == "":
            canonical_smi = cls.canonicalize_smiles(smi)
            return TranslatedMolecule(
                smi=canonical_smi,
                groups="",
                caption=caption,
                esmi=cls.build_esmi(canonical_smi),
                markush=False,
                sru=False,
            )

        groups, ext = cls.parse_trailing(trailing)

        try:
            abbrev_map = get_abbrev_smi()
            raw_mol = Chem.MolFromSmiles(smi)
            if raw_mol is None:
                if error_msg:
                    logger.warning(f"Invalid SMILES: {smi}")
                return TranslatedMolecule(
                    smi=smi,
                    groups=groups,
                    caption=caption,
                    esmi=cls.build_esmi(smi, groups, ext),
                    markush=len(groups) > 0,
                    sru=False,
                )
            repaired_groups = cls.repair_atom_group_indices(raw_mol, groups, error_msg=error_msg)
            if repaired_groups != groups:
                groups = repaired_groups
                caption = cls.build_esmi(smi, groups, ext)
            stereo_safe_smi, temporary_isotopes = cls._protect_stereo_for_dummy_substitution(
                smi, groups, abbrev_map, raw_mol, error_msg=error_msg
            )
            mol = raw_mol if stereo_safe_smi == smi else Chem.MolFromSmiles(stereo_safe_smi)
            if mol is None:
                if error_msg:
                    logger.warning(f"Invalid SMILES: {smi}")
                return TranslatedMolecule(
                    smi=smi,
                    groups=groups,
                    caption=caption,
                    esmi=cls.build_esmi(smi, groups, ext),
                    markush=len(groups) > 0,
                    sru=False,
                )
            for atom in mol.GetAtoms():
                atom.SetAtomMapNum(atom.GetIdx() + 1)
            ring_info = mol.GetRingInfo().AtomRings()
            mapped_smiles = Chem.MolToSmiles(mol, canonical=False, isomericSmiles=True)
            mapped_caption = cls.build_esmi(mapped_smiles, groups, ext)

            parsed = cls.parse_caption(
                mapped_caption, return_mol=True, error_msg=error_msg
            )
            mol, groups, ext = parsed
            mol = Chem.RWMol(mol)

            mapnum2idx: Dict[int, int] = {}
            for a in mol.GetAtoms():
                mn = a.GetAtomMapNum()
                if mn > 0:
                    mapnum2idx[mn] = a.GetIdx()

            to_remove: List[int] = []
            consumed_atom_groups: set[int] = set()
            preserve_dummy_groups = False
            preserve_precompat_groups = any(
                token in groups
                for token in (
                    Tokens.substruct_start,
                    Tokens.sgroup_start,
                    Tokens.virtual_start,
                    f"{Tokens.ring_start}{Tokens.virtual_start}",
                )
            )
            is_markush = False
            is_sru = False

            if cls.parse_extension(ext) == "Sg:n":
                is_sru = True

            for desc in cls.parse_groups(groups):
                if not isinstance(desc.id, AtomIndex) or desc.is_circle:
                    is_markush = True
                    continue

                atom_idx = mapnum2idx.get(int(desc.id) + 1)
                if atom_idx is None:
                    is_markush = True
                    continue

                atom = mol.GetAtomWithIdx(atom_idx)
                if atom.GetSymbol() != "*" and not desc.is_dummy:
                    is_markush = True
                    continue

                if desc.is_dummy:
                    preserve_dummy_groups = True
                    continue

                if not desc.symbol:
                    continue

                # Carbon chain repetition, e.g. (CH2)n.
                if (desc.symbol == "CH2") or (desc.symbol == "CH" and desc.script == "2"):
                    if desc.multiple and not desc.multiple.isdigit():
                        is_markush = True
                        continue
                    is_markush = carbon_chain_repetition_process(
                        mol, atom_idx, desc, is_markush, error_msg=False
                    )
                    consumed_atom_groups.add(int(desc.id))
                    continue

                # Build lookup key, e.g. NO + 2 -> NO2.
                lookup_symbol = desc.symbol
                if desc.script:
                    lookup_symbol += desc.script

                if lookup_symbol == "CN":
                    src_smi = "C(#N)"
                else:
                    src_smi = abbrev_map.get(lookup_symbol)

                if src_smi is not None:
                    src_mol_check = Chem.MolFromSmiles(src_smi)
                    if src_mol_check and src_mol_check.GetNumAtoms() == 1:
                        alter_atom(atom, smiles=src_smi)
                        consumed_atom_groups.add(int(desc.id))
                        continue

                    if atom.GetDegree() != 1:
                        if error_msg:
                            logger.warning(
                                f"Group `{lookup_symbol}` cannot be attached to atom "
                                f"{atom_idx} with degree {atom.GetDegree()}"
                            )
                        is_markush = True
                        continue

                    if any(not is_single_bond(b) for b in atom.GetBonds()):
                        if error_msg:
                            logger.warning(
                                f"Group `{lookup_symbol}` must link to single bond"
                            )
                        is_markush = True
                        continue

                    try:
                        merge_group(tgt_mol=mol, src=src_smi, attach_idx=atom_idx)
                        to_remove.append(atom_idx)
                        consumed_atom_groups.add(int(desc.id))
                    except Exception as e:
                        if error_msg:
                            logger.warning(f"Merge group failed: {e}")
                        is_markush = True
                    continue

                if lookup_symbol in PERIODIC_TABLE and not desc.multiple:
                    try:
                        alter_atom(atom, smiles=None, element=lookup_symbol)
                        consumed_atom_groups.add(int(desc.id))
                    except Exception as e:
                        if error_msg:
                            logger.warning(
                                f"Failed to mutate atom {atom_idx} to {lookup_symbol}: {e}"
                            )
                        is_markush = True
                    continue

                is_markush = True

            for i in sorted(to_remove, reverse=True):
                mol.RemoveAtom(i)

            cleared_temporary_isotopes = False
            for atom in mol.GetAtoms():
                if atom.GetSymbol() == "*" and atom.GetIsotope() in temporary_isotopes:
                    atom.SetIsotope(0)
                    cleared_temporary_isotopes = True

            try:
                Chem.SanitizeMol(mol)
                if cleared_temporary_isotopes:
                    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
            except Exception as e:
                if error_msg:
                    logger.error(f"Sanitize failed: {e}")
                return TranslatedMolecule(
                    smi=smi,
                    groups=groups,
                    caption=caption,
                    esmi=cls.build_esmi(smi, groups, ext),
                    markush=True,
                    sru=is_sru,
                )

            mol = Chem.MolFromSmiles(
                Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
            )

            if is_markush or preserve_dummy_groups or preserve_precompat_groups:
                remaining_groups = cls.remove_atom_groups(groups, consumed_atom_groups)
                new_groups = remap_groups(mol, remaining_groups, ring_info)
            else:
                new_groups = ""
                for atom in mol.GetAtoms():
                    atom.SetAtomMapNum(0)

            new_smi = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
            new_esmi = cls.build_esmi(new_smi, new_groups, ext)

            return TranslatedMolecule(
                smi=new_smi,
                groups=new_groups,
                caption=caption,
                esmi=new_esmi,
                markush=is_markush,
                sru=is_sru,
            )
        except Exception as e:
            if error_msg:
                logger.error(f"Error while refactoring SMILES: {smi}")
                logger.error(repr(e))
            return TranslatedMolecule(
                smi=smi,
                groups=groups,
                caption=caption,
                esmi=cls.build_esmi(smi, groups, ext),
                markush=len(groups) > 0,
                sru=False,
            )


# ===========================================================================
# markush —— Markush 组展开（substitute_markush）
# ===========================================================================
DefinitionValue = int | str | Sequence[str]
_PRECOMPAT_RECORD_PATTERN = re.compile(
    r"<s>.*?</s>|<g>.*?</g>|<r><v>\d+:.+?</r>|<v>.*?</v>",
    re.DOTALL,
)
_SUBSTRUCT_RECORD_PATTERN = re.compile(r"^<s>(?P<body>.*)</s>$", re.DOTALL)


def _normalize_label(label: str) -> str:
    return label.replace("[", "").replace("]", "").strip()


def _definition_lookup(definitions: Mapping[str, DefinitionValue]) -> dict[str, DefinitionValue]:
    lookup: dict[str, DefinitionValue] = {}
    for key, value in definitions.items():
        clean_key = str(key).strip()
        lookup[clean_key] = value
        lookup[_normalize_label(clean_key)] = value
    return lookup


def _group_labels(desc: GroupDesc) -> list[str]:
    if not desc.symbol:
        return []
    labels = [desc.symbol]
    if desc.script:
        labels = [f"{desc.symbol}[{desc.script}]", f"{desc.symbol}{desc.script}"]
    return [label + (desc.prime or "") for label in labels]


def _as_values(value: DefinitionValue) -> list[str]:
    if isinstance(value, (int, str)):
        return [str(value)]
    return [str(item) for item in value]


def _resolve_group_smiles(
    desc: GroupDesc,
    definitions: Mapping[str, DefinitionValue],
) -> list[str]:
    definition_lookup = _definition_lookup(definitions)
    for label in _group_labels(desc):
        value = definition_lookup.get(label)
        if value is None:
            value = definition_lookup.get(_normalize_label(label))
        if value is not None:
            return [_resolve_fragment_smiles(v) for v in _as_values(value)]

    lookup_symbol = desc.symbol or ""
    if desc.script:
        lookup_symbol += desc.script
    src = get_abbrev_smi().get(lookup_symbol)
    if src is not None:
        return [src]
    return []


def _resolve_fragment_smiles(value: str) -> str:
    value = str(value).strip()
    if Tokens.separator in value:
        value = value.split(Tokens.separator, 1)[0].strip()
    return get_abbrev_smi().get(value, value)


def _repeat_counts(desc: GroupDesc, site_count: int) -> list[int]:
    if not desc.multiple:
        return [1]
    if desc.multiple.isdigit():
        return [int(desc.multiple)]
    if desc.multiple == "n":
        return list(range(1, site_count + 1))
    if "-" in desc.multiple:
        start, end = desc.multiple.split("-", 1)
        if start.isdigit() and end.isdigit():
            return list(range(int(start), int(end) + 1))
    raise ValueError(f"Unsupported multiplicity: ?{desc.multiple}")


def _resolve_repetition_count(
    desc: GroupDesc,
    definitions: Mapping[str, DefinitionValue],
) -> int | None:
    if not desc.multiple:
        return None
    if desc.multiple.isdigit():
        return int(desc.multiple)

    value = _definition_lookup(definitions).get(desc.multiple)
    if isinstance(value, Sequence) and not isinstance(value, str):
        raise ValueError(f"Multiplicity ?{desc.multiple} must resolve to one integer")
    if value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Multiplicity ?{desc.multiple} must resolve to one integer") from exc
    if count < 1:
        raise ValueError(f"Multiplicity ?{desc.multiple} must be positive")
    return count


def _is_carbon_chain_repeat(desc: GroupDesc) -> bool:
    return (desc.symbol == "CH2") or (desc.symbol == "CH" and desc.script == "2")


def _is_special_id_label(desc: GroupDesc) -> bool:
    return desc.symbol == Tokens.special_id


def _find_atom_by_source_index(mol: Chem.rdchem.Mol, source_idx: int) -> int | None:
    map_num = source_idx + 1
    for atom in mol.GetAtoms():
        if atom.GetAtomMapNum() == map_num:
            return atom.GetIdx()
    return None


def _source_attachment(src_mol: Chem.rdchem.Mol) -> tuple[int, int | None]:
    """返回片段中 * 连接点：(* 的邻居 idx, * 自身 idx)。"""
    dummy_atoms = [atom for atom in src_mol.GetAtoms() if atom.GetSymbol() == "*"]
    if not dummy_atoms:
        return 0, None
    if len(dummy_atoms) != 1:
        raise ValueError("Substituent SMILES must contain at most one `*` attachment atom")
    dummy = dummy_atoms[0]
    neighbors = dummy.GetNeighbors()
    if len(neighbors) != 1:
        raise ValueError("Substituent `*` attachment atom must have exactly one neighbor")
    return neighbors[0].GetIdx(), dummy.GetIdx()


def _copy_fragment(
    tgt_mol: Chem.rdchem.RWMol,
    src_mol: Chem.rdchem.Mol,
    omitted_idx: int | None,
) -> dict[int, int]:
    idx_map: dict[int, int] = {}
    for atom in src_mol.GetAtoms():
        if atom.GetIdx() == omitted_idx:
            continue
        new_atom = Chem.Atom(atom)
        new_atom.SetAtomMapNum(0)
        idx_map[atom.GetIdx()] = tgt_mol.AddAtom(new_atom)

    for bond in src_mol.GetBonds():
        begin = bond.GetBeginAtomIdx()
        end = bond.GetEndAtomIdx()
        if begin == omitted_idx or end == omitted_idx:
            continue
        tgt_mol.AddBond(idx_map[begin], idx_map[end], bond.GetBondType())
    return idx_map


def _attach_fragment(
    mol: Chem.rdchem.RWMol,
    attach_idx: int,
    fragment_smiles: str,
) -> None:
    """把片段挂到 attach_idx：若目标为 * 则并入并移除星号。"""
    src_mol = Chem.MolFromSmiles(fragment_smiles)
    if src_mol is None:
        raise ValueError(f"Invalid substituent SMILES: {fragment_smiles}")

    src_attach_idx, src_dummy_idx = _source_attachment(src_mol)
    attach_atom = mol.GetAtomWithIdx(attach_idx)
    replace_dummy = attach_atom.GetSymbol() == "*"
    target_idx = attach_idx

    if replace_dummy:
        neighbors = attach_atom.GetNeighbors()
        if len(neighbors) != 1:
            raise ValueError("Target `*` attachment atom must have exactly one neighbor")
        neighbor = neighbors[0]
        bond = mol.GetBondBetweenAtoms(attach_idx, neighbor.GetIdx())
        if bond is None or bond.GetBondType() != Chem.BondType.SINGLE:
            raise ValueError("Target `*` attachment atom must link through a single bond")
        target_idx = neighbor.GetIdx()
        mol.RemoveBond(attach_idx, target_idx)

    idx_map = _copy_fragment(mol, src_mol, src_dummy_idx)
    mol.AddBond(target_idx, idx_map[src_attach_idx], Chem.BondType.SINGLE)

    if replace_dummy:
        mol.RemoveAtom(attach_idx)


def _canonical_smiles(mol: Chem.rdchem.Mol) -> str:
    output = Chem.Mol(mol)
    for atom in output.GetAtoms():
        atom.SetAtomMapNum(0)
    Chem.SanitizeMol(output)
    output = Chem.RemoveHs(output)
    return Chem.MolToSmiles(output, canonical=True, isomericSmiles=True)


def _collect_valid_smiles(states: list[Chem.rdchem.RWMol]) -> list[str]:
    RDLogger.DisableLog("rdApp.*")
    try:
        smiles = set()
        for state in states:
            try:
                smiles.add(_canonical_smiles(state))
            except Exception:
                continue
        return sorted(smiles)
    finally:
        RDLogger.EnableLog("rdApp.*")


def _ring_atom_indices(
    mol: Chem.rdchem.Mol,
    source_ring: Iterable[int],
) -> list[int]:
    indices: list[int] = []
    for source_atom_idx in source_ring:
        atom_idx = _find_atom_by_source_index(mol, source_atom_idx)
        if atom_idx is not None:
            indices.append(atom_idx)
    return indices


def _expand_atom_group(
    states: list[Chem.rdchem.RWMol],
    desc: GroupDesc,
    fragments: list[str],
) -> list[Chem.rdchem.RWMol]:
    counts = _repeat_counts(desc, 1)
    if counts != [1]:
        raise ValueError("Atom-indexed Markush groups only support single substitution")

    next_states: list[Chem.rdchem.RWMol] = []
    for state in states:
        attach_idx = _find_atom_by_source_index(state, int(desc.id))
        if attach_idx is None:
            raise ValueError(f"Atom index {int(desc.id)} is not present in the molecule")
        for fragment in fragments:
            new_state = Chem.RWMol(state)
            _attach_fragment(new_state, attach_idx, fragment)
            next_states.append(new_state)
    return next_states


def _source_ring_for_atom(
    source_rings: tuple[tuple[int, ...], ...],
    source_atom_idx: int,
) -> tuple[int, ...] | None:
    for source_ring in source_rings:
        if source_atom_idx in source_ring:
            return source_ring
    return None


def _expand_atom_group_copies(
    states: list[Chem.rdchem.RWMol],
    desc: GroupDesc,
    fragments: list[str],
    definitions: Mapping[str, DefinitionValue],
    source_rings: tuple[tuple[int, ...], ...],
    max_outputs: int,
) -> list[Chem.rdchem.RWMol] | None:
    count = _resolve_repetition_count(desc, definitions)
    if count is None:
        return None
    if count == 1:
        return _expand_atom_group(states, desc, fragments)

    next_states: list[Chem.rdchem.RWMol] = []
    for state in states:
        attach_idx = _find_atom_by_source_index(state, int(desc.id))
        if attach_idx is None:
            raise ValueError(f"Atom index {int(desc.id)} is not present in the molecule")

        attach_atom = state.GetAtomWithIdx(attach_idx)
        if attach_atom.GetSymbol() == "*":
            neighbors = attach_atom.GetNeighbors()
            if len(neighbors) != 1:
                raise ValueError("Target `*` attachment atom must have exactly one neighbor")
            ring_anchor_idx = neighbors[0].GetIdx()
        else:
            ring_anchor_idx = attach_idx

        anchor_map_num = state.GetAtomWithIdx(ring_anchor_idx).GetAtomMapNum()
        source_ring = _source_ring_for_atom(source_rings, anchor_map_num - 1)
        if source_ring is None:
            raise ValueError(
                f"Cannot copy atom-indexed group `{str(desc)}` without an anchor ring"
            )

        sites = [
            site
            for site in _ring_atom_indices(state, source_ring)
            if site != ring_anchor_idx
        ]
        if count - 1 > len(sites):
            raise ValueError(
                f"Cannot place {count} copies of `{str(desc)}` on the anchor ring"
            )

        for extra_sites in combinations(sites, count - 1):
            for fragment in fragments:
                new_state = Chem.RWMol(state)
                for site in extra_sites:
                    _attach_fragment(new_state, site, fragment)
                _attach_fragment(new_state, attach_idx, fragment)
                next_states.append(new_state)
                if len(next_states) > max_outputs:
                    raise ValueError(f"Markush expansion exceeded max_outputs={max_outputs}")
    return next_states


def _expand_atom_repetition(
    states: list[Chem.rdchem.RWMol],
    desc: GroupDesc,
    definitions: Mapping[str, DefinitionValue],
) -> list[Chem.rdchem.RWMol]:
    count = _resolve_repetition_count(desc, definitions)
    if count is None:
        raise ValueError(f"No repetition count found for group `{str(desc)}`")

    repeated_desc = GroupDesc(
        id=desc.id,
        symbol=desc.symbol,
        script=desc.script,
        prime=desc.prime,
        multiple=str(count),
        is_circle=desc.is_circle,
        is_dummy=desc.is_dummy,
    )
    next_states: list[Chem.rdchem.RWMol] = []
    for state in states:
        atom_idx = _find_atom_by_source_index(state, int(desc.id))
        if atom_idx is None:
            raise ValueError(f"Atom index {int(desc.id)} is not present in the molecule")
        new_state = Chem.RWMol(state)
        is_markush = carbon_chain_repetition_process(
            new_state,
            atom_idx,
            repeated_desc,
            is_markush=False,
        )
        if is_markush:
            raise ValueError(f"Failed to expand carbon-chain repeat `{str(desc)}`")
        next_states.append(new_state)
    return next_states


def _expand_ring_group(
    states: list[Chem.rdchem.RWMol],
    desc: GroupDesc,
    fragments: list[str],
    source_rings: tuple[tuple[int, ...], ...],
    max_outputs: int,
) -> list[Chem.rdchem.RWMol]:
    source_ring = source_rings[int(desc.id)]
    next_states: list[Chem.rdchem.RWMol] = []

    for state in states:
        sites = _ring_atom_indices(state, source_ring)
        if not sites:
            raise ValueError(f"Ring index {int(desc.id)} is not present in the molecule")
        for count in _repeat_counts(desc, len(sites)):
            if count < 1 or count > len(sites):
                continue
            for site_group in combinations(sites, count):
                for fragment in fragments:
                    new_state = Chem.RWMol(state)
                    for site in site_group:
                        _attach_fragment(new_state, site, fragment)
                    next_states.append(new_state)
                    if len(next_states) > max_outputs:
                        raise ValueError(
                            f"Markush expansion exceeded max_outputs={max_outputs}"
                        )
    return next_states


def _format_substitution_outputs(
    smiles: list[str],
    annotation_variants: list[str],
    ext: str,
    force_esmiles: bool,
) -> list[str]:
    if not force_esmiles and not annotation_variants:
        return smiles

    annotations = annotation_variants or [""]
    return [
        Translator.build_esmi(smile, annotation, ext)
        for smile in smiles
        for annotation in annotations
    ]


def _format_preserved_group_outputs(
    states: list[Chem.rdchem.RWMol],
    preserved_groups: str,
    source_rings: tuple[tuple[int, ...], ...],
    annotation_variants: list[str],
    ext: str,
) -> list[str]:
    outputs: set[str] = set()
    annotations = annotation_variants or [""]
    for state in states:
        smiles = _canonical_smiles(state)
        remap_mol = Chem.Mol(state)
        Chem.SanitizeMol(remap_mol)
        remapped_groups = remap_groups(
            remap_mol,
            preserved_groups,
            source_rings,
        )
        for annotation in annotations:
            outputs.add(Translator.build_esmi(smiles, remapped_groups + annotation, ext))
    return sorted(outputs)


def _substitute_precompat_records(
    groups: str,
    definitions: Mapping[str, DefinitionValue],
    *,
    max_outputs: int,
    error_msg: bool,
) -> list[str]:
    variants = [""]
    found_record = False
    for match in _PRECOMPAT_RECORD_PATTERN.finditer(groups):
        found_record = True
        record = match.group(0)
        substruct_match = _SUBSTRUCT_RECORD_PATTERN.match(record)
        if substruct_match:
            body = substruct_match.group("body")
            body_variants = _substitute_markush_outputs(
                body,
                definitions,
                max_outputs=max_outputs,
                error_msg=error_msg,
                force_esmiles=True,
            )
            record_variants = [f"<s>{body_variant}</s>" for body_variant in body_variants]
        else:
            record_variants = [record]

        variants = [
            prefix + record_variant
            for prefix in variants
            for record_variant in record_variants
        ]
        if len(variants) > max_outputs:
            raise ValueError(f"Markush expansion exceeded max_outputs={max_outputs}")
    return variants if found_record else []


def _substitute_markush_outputs(
    caption: str,
    definitions: Mapping[str, DefinitionValue],
    *,
    max_outputs: int,
    error_msg: bool,
    force_esmiles: bool = False,
) -> list[str]:
    raw_caption = str(caption).strip()
    parsed = Translator.parse_caption(raw_caption, return_mol=True, error_msg=error_msg)
    if parsed is None:
        raise ValueError(f"Invalid E-SMILES caption: {caption}")

    mol, groups, ext = parsed
    annotation_variants = _substitute_precompat_records(
        groups,
        definitions,
        max_outputs=max_outputs,
        error_msg=error_msg,
    )
    groups = Translator.repair_atom_group_indices(mol, groups, error_msg=error_msg)
    source_rings = mol.GetRingInfo().AtomRings()
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(atom.GetIdx() + 1)

    states = [Chem.RWMol(mol)]
    special_id_groups: list[str] = []
    for desc in Translator.parse_groups(groups):
        if desc.is_dummy or desc.is_circle:
            continue
        if _is_special_id_label(desc):
            special_id_groups.append(f"{Tokens.atom_start}{int(desc.id)}:{str(desc)}{Tokens.atom_end}")
            continue
        if isinstance(desc.id, RingIndex) and desc.id.virtual:
            continue
        if isinstance(desc.id, AtomIndex) and _is_carbon_chain_repeat(desc):
            states = _expand_atom_repetition(states, desc, definitions)
            continue

        fragments = _resolve_group_smiles(desc, definitions)
        if not fragments:
            raise ValueError(f"No Markush definition found for group `{str(desc)}`")

        if isinstance(desc.id, AtomIndex) and len(states) * len(fragments) > max_outputs:
            raise ValueError(f"Markush expansion exceeded max_outputs={max_outputs}")
        if isinstance(desc.id, AtomIndex):
            copied_states = None
            if desc.multiple:
                copied_states = _expand_atom_group_copies(
                    states,
                    desc,
                    fragments,
                    definitions,
                    source_rings,
                    max_outputs,
                )
            states = copied_states if copied_states is not None else _expand_atom_group(
                states,
                desc,
                fragments,
            )
        elif isinstance(desc.id, RingIndex):
            if int(desc.id) >= len(source_rings):
                raise ValueError(f"Ring index {int(desc.id)} is out of range")
            states = _expand_ring_group(
                states,
                desc,
                fragments,
                source_rings,
                max_outputs=max_outputs,
            )

        if len(states) > max_outputs:
            raise ValueError(f"Markush expansion exceeded max_outputs={max_outputs}")

    smiles = _collect_valid_smiles(states)
    if not smiles:
        raise ValueError("No valid SMILES generated from Markush substitution")
    if special_id_groups:
        return _format_preserved_group_outputs(
            states,
            "".join(special_id_groups),
            source_rings,
            annotation_variants,
            ext,
        )
    return _format_substitution_outputs(smiles, annotation_variants, ext, force_esmiles)


def substitute_markush(
    caption: str,
    definitions: Mapping[str, DefinitionValue],
    *,
    max_outputs: int = 1024,
    error_msg: bool = False,
) -> str | list[str]:
    """把 E-SMILES 中的 Markush 基团按 definitions 展开成具体分子。

    参数
    ----
    caption : E-SMILES，形如 ``SMILES<sep><a>3:R1</a><r>1:R2</r>``。
    definitions : 键为基团标签（``R1`` 或 ``R[1]`` 均可，自动归一化），
                  值为 SMILES 片段、缩写名（``Me``/``CF3``）或它们的列表。
                  片段含 ``*`` 时该原子作为连接点，拼接时移除星号。
    max_outputs : 展开产物上限，超出抛 ``ValueError``。
    error_msg : 是否输出详细 rdkit 日志。

    返回 : 单条结果时返回 ``str``，多条时返回 ``list[str]``。
           默认输出标准 SMILES；若标注含 ``<s>`` 等 precompat 记录，
           或调用内部 ``_substitute_markush_outputs(force_esmiles=True)``，
           则输出保留标注的 E-SMILES。
    """
    outputs = _substitute_markush_outputs(
        str(caption).strip(),
        definitions,
        max_outputs=max_outputs,
        error_msg=error_msg,
    )
    if len(outputs) == 1:
        return outputs[0]
    return outputs
