"""Skill 包加载与校验（执行方案 S3.1，设计文档 9.2、9.6.9 节）。

`load_package(path) -> LoadedSkill` 读取 `manifest.yaml`、`SKILL.md`（frontmatter = L0，固定标题小节 = L1）、
schemas、prompts（Jinja2，StrictUndefined）、references（L3，按小节索引），并计算：

- `content_hash`：目录下所有文件按相对路径排序后逐个哈希，再汇总；
- `prompt_hash`：所有模板与被引用公共片段的哈希。

每类问题一个错误码（`E_*` 拒绝激活，`W_*` 仅警告）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import jinja2
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from drugpaper.capabilities.assembler import PROMPTS_ROOT
from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.skills.protocol import Scope, SkillManifest

L1_SECTIONS = ["何时使用", "何时不用", "输入", "输出", "代价与限制"]
L1_REQUIRED = ["何时使用", "何时不用", "输入", "输出"]
ALWAYS_KNOWN_TOOLS = frozenset({"model.call"})
IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".DS_Store"})

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)

_JINJA = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False, keep_trailing_newline=True)
_JINJA.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}


@dataclass
class LoadIssue:
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass
class LoadedSkill:
    path: Path
    scope: Scope
    manifest: SkillManifest | None = None
    l0: str = ""
    l1_sections: dict[str, str] = field(default_factory=dict)
    resources_index: dict[str, list[str]] = field(default_factory=dict)  # 文件 → 小节标题
    prompts: dict[str, str] = field(default_factory=dict)  # 模板名 → 源码
    fragments: dict[str, str] = field(default_factory=dict)  # 公共片段名 → 内容
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    package_hash: str = ""
    content_hash: str = ""
    prompt_hash: str = ""
    errors: list[LoadIssue] = field(default_factory=list)
    warnings: list[LoadIssue] = field(default_factory=list)
    _compiled: dict[str, jinja2.Template] = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return not self.errors and self.manifest is not None

    @property
    def name(self) -> str:
        return self.manifest.name if self.manifest else self.path.name

    @property
    def version(self) -> str:
        return self.manifest.version if self.manifest else "0.0.0"

    @property
    def skill_id(self) -> str:
        """版本标识 `name@version#content_hash`（取哈希前 12 位）。"""
        return f"{self.name}@{self.version}#{self.content_hash[:12]}"

    @property
    def l1(self) -> str:
        return "\n".join(f"【{k}】{v}" for k, v in self.l1_sections.items() if k in L1_SECTIONS)

    @property
    def l0_tokens(self) -> int:
        return estimate_tokens(self.l0)

    @property
    def l1_tokens(self) -> int:
        return estimate_tokens(self.l1)

    def error_codes(self) -> list[str]:
        return [e.code for e in self.errors]

    def render(self, template: str, **variables: Any) -> str:
        if template not in self._compiled:
            self._compiled[template] = _JINJA.from_string(self.prompts[template])
        return self._compiled[template].render(**variables)

    def system_text(self) -> str:
        """系统规则：公共片段按 includes 顺序拼接（固定前缀）+ 可选 system 模板。"""
        parts = list(self.fragments.values())
        if "system" in self.prompts:
            parts.append(self.prompts["system"])
        return "\n\n".join(p.strip() for p in parts if p.strip())

    def read_resource(self, section: str, max_tokens: int = 1500) -> str:
        """`file#小节` 或 `小节` → 该小节正文（≤ max_tokens，超出截断并标注）。"""
        file_part, _, sec = section.partition("#")
        # Cards may show package-relative references/foo.md; the resource index
        # itself is relative to references/. Both spellings stay sandboxed.
        file_part = file_part.removeprefix("references/")
        candidates = [file_part] if sec else list(self.resources_index)
        target = sec or file_part
        for rel in candidates:
            p = self.path / "references" / rel
            if (
                rel not in self.resources_index
                or not p.resolve().is_relative_to((self.path / "references").resolve())
                or not p.is_file()
            ):
                continue
            sections = split_sections(p.read_text(encoding="utf-8"))
            if target in sections or (not sec and rel == target):
                text = sections.get(target) or p.read_text(encoding="utf-8")
                if estimate_tokens(text) > max_tokens:
                    suffix = "…"
                    text = text[:max_tokens]
                    while text and estimate_tokens(text + suffix) > max_tokens:
                        text = text[:-1]
                    text += suffix
                return text
        raise KeyError(f"{self.name} 没有参考资料小节：{section}")


def split_sections(markdown: str) -> dict[str, str]:
    """按 `##`/`###` 标题切分，返回 {标题: 正文}。"""
    out: dict[str, str] = {}
    matches = list(_HEADING.finditer(markdown))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        out[m.group(2).strip()] = markdown[m.end() : end].strip()
    return out


def case_files(root: Path) -> list[Path]:
    """测试用例：tests/cases/*.json（不含 *.response.json 回放文件）。"""
    d = root / "tests" / "cases"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if not p.name.endswith(".response.json"))


def directory_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*"), key=lambda x: x.relative_to(root).as_posix()):
        if not p.is_file() or IGNORED_PARTS & set(p.relative_to(root).parts) or p.suffix == ".pyc":
            continue
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _within(root: Path, rel: str) -> Path | None:
    p = (root / rel).resolve()
    return p if p.is_relative_to(root.resolve()) else None


def load_package(
    path: Path | str,
    *,
    scope: Scope = "builtin",
    known_tools: set[str] | None = None,
    known_tasks: set[str] | None = None,
    prompts_root: Path = PROMPTS_ROOT,
) -> LoadedSkill:
    root = Path(path)
    sk = LoadedSkill(path=root, scope=scope)

    def err(code: str, msg: str) -> None:
        sk.errors.append(LoadIssue(code, msg))

    def warn(code: str, msg: str) -> None:
        sk.warnings.append(LoadIssue(code, msg, "warning"))

    if not root.is_dir():
        err("E_NOT_A_DIRECTORY", f"{root} 不是目录")
        return sk
    sk.package_hash = directory_hash(root)
    sk.content_hash = sk.package_hash

    # ---- manifest
    mf = root / "manifest.yaml"
    if not mf.is_file():
        err("E_MANIFEST_MISSING", "缺少 manifest.yaml")
        return sk
    try:
        raw = yaml.safe_load(mf.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        err("E_MANIFEST_YAML", f"manifest.yaml 不是合法 YAML：{e}")
        return sk
    try:
        m = SkillManifest.model_validate(raw)
    except ValidationError as e:
        details = "; ".join(f"{'.'.join(str(x) for x in x_['loc'])}: {x_['msg']}" for x_ in e.errors()[:8])
        err("E_MANIFEST_SCHEMA", f"manifest 字段校验失败：{details}")
        return sk
    sk.manifest = m
    if m.name != root.name:
        err("E_NAME_MISMATCH", f"manifest.name={m.name!r} 与目录名 {root.name!r} 不一致")
    if m.scope is not None and m.scope != scope:
        err("E_SCOPE_MISMATCH", f"manifest.scope={m.scope} 与加载位置的作用域 {scope} 不一致")

    # ---- SKILL.md
    md = root / "SKILL.md"
    if not md.is_file():
        err("E_SKILL_MD_MISSING", "缺少 SKILL.md")
    else:
        fm = _FRONTMATTER.match(md.read_text(encoding="utf-8"))
        if fm is None:
            err("E_FRONTMATTER", "SKILL.md 缺少 frontmatter（--- name / description ---）")
        else:
            try:
                meta = yaml.safe_load(fm.group(1)) or {}
            except yaml.YAMLError as e:
                meta = {}
                err("E_FRONTMATTER", f"frontmatter 不是合法 YAML：{e}")
            if meta.get("name") not in (None, m.name):
                err("E_NAME_MISMATCH", f"SKILL.md name={meta.get('name')!r} 与 manifest 不一致")
            sk.l0 = str(meta.get("description") or "").strip()
            if not sk.l0:
                err("E_FRONTMATTER", "SKILL.md frontmatter 缺少 description（L0）")
            sk.l1_sections = {k: v for k, v in split_sections(fm.group(2)).items() if v}
            missing = [s for s in L1_REQUIRED if s not in sk.l1_sections]
            if missing:
                warn("W_L1_SECTION_MISSING", f"L1 能力卡缺少小节：{', '.join(missing)}")
        budgets = m.disclosure.budgets
        if sk.l0 and sk.l0_tokens > min(40, budgets.l0_tokens):
            err("E_L0_BUDGET", f"L0 约 {sk.l0_tokens} tokens，超过上限 {budgets.l0_tokens}")
        if sk.l1_tokens > min(350, budgets.l1_tokens):
            err("E_L1_BUDGET", f"L1 约 {sk.l1_tokens} tokens，超过上限 {budgets.l1_tokens}")

    # ---- schemas
    for attr, rel in (("input_schema", m.input_schema), ("output_schema", m.output_schema)):
        p = _within(root, rel)
        if p is None or not p.is_file():
            err("E_SCHEMA_FILE", f"{attr} 文件不存在：{rel}")
            continue
        try:
            schema = json.loads(p.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        except (json.JSONDecodeError, SchemaError) as e:
            err("E_SCHEMA_INVALID", f"{attr}（{rel}）不是合法的 JSON Schema：{str(e).splitlines()[0]}")
            continue
        setattr(sk, attr, schema)

    # ---- prompts
    templates = m.prompts.templates()
    if m.kind == "prompt" and "main" not in templates:
        err("E_PROMPT_REQUIRED", "prompt 型 Skill 必须声明 prompts.main")
    for key, rel in templates.items():
        p = _within(root, rel)
        if p is None or not p.is_file():
            err("E_PROMPT_MISSING", f"模板 {key} 不存在：{rel}")
            continue
        src = p.read_text(encoding="utf-8")
        try:
            _JINJA.parse(src)
        except jinja2.TemplateSyntaxError as e:
            err("E_PROMPT_COMPILE", f"模板 {key}（{rel}）编译失败：第 {e.lineno} 行 {e.message}")
            continue
        sk.prompts[key] = src
    for inc in m.prompts.includes:
        p = _within(prompts_root, inc if inc.endswith(".md") else inc + ".md")
        if p is None or not p.is_file():
            err("E_INCLUDE_MISSING", f"引用的公共片段不存在：{inc}")
            continue
        sk.fragments[inc] = p.read_text(encoding="utf-8").strip()
    ph = hashlib.sha256()
    for k in sorted(sk.fragments):
        ph.update(f"fragment:{k}\0{sk.fragments[k]}\0".encode())
    for k in sorted(sk.prompts):
        ph.update(f"template:{k}\0{sk.prompts[k]}\0".encode())
    sk.prompt_hash = ph.hexdigest()
    sk.content_hash = hashlib.sha256((sk.package_hash + sk.prompt_hash).encode()).hexdigest()

    # ---- 入口
    if m.kind == "python" and not m.entry:
        err("E_ENTRY_MISSING", "python 型 Skill 必须声明 entry")
    for hook in (m.entry, m.prepare, m.postprocess):
        if hook:
            p = _within(root, hook.split(":", 1)[0])
            if p is None or not p.is_file():
                err("E_ENTRY_MISSING", f"入口文件不存在：{hook}")
    if m.kind == "graph":
        err("E_KIND_UNSUPPORTED", "graph 型 Skill 首版只给内置编排使用，暂不支持从包加载")

    # ---- 引用
    if m.kind == "prompt" and not m.model_tasks:
        err(
            "E_TASK_REQUIRED", "prompt 型 Skill 必须在 model_tasks 中声明模型任务（如 main: visual_classify）"
        )
    if known_tasks is not None:
        for key, task in m.model_tasks.items():
            if task not in known_tasks:
                err("E_TASK_UNKNOWN", f"model_tasks.{key}={task!r} 未在模型注册表中定义")
    if known_tools is not None:
        for t in m.tools:
            if t not in known_tools and t not in ALWAYS_KNOWN_TOOLS:
                err("E_TOOL_UNKNOWN", f"tools 中的 {t!r} 未注册")

    # ---- L3
    ref_dir = root / "references"
    if ref_dir.is_dir():
        for p in sorted(ref_dir.rglob("*.md")):
            sk.resources_index[p.relative_to(ref_dir).as_posix()] = list(
                split_sections(p.read_text(encoding="utf-8"))
            )
    if not case_files(root):
        warn("W_NO_TESTS", "tests/cases 下没有用例")
    return sk
