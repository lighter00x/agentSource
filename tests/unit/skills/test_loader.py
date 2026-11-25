"""S3.1：Skill 包加载与校验。非法包由合法样例复制后修改生成，每类问题对应一个错误码。"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from drugpaper.skills.loader import directory_hash, load_package, split_sections

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "skills"
TOOLS = {"get_node", "grep"}
TASKS = {"default", "visual_classify"}


def test_valid_packages() -> None:
    p = load_package(FIX / "demo_prompt", known_tools=TOOLS, known_tasks=TASKS)
    assert p.ok, p.errors
    assert p.l0 == "测试用：给图表节点打一个标签。" and p.l0_tokens <= 40
    assert set(p.l1_sections) >= {"何时使用", "何时不用", "输入", "输出"}
    assert "common/data_boundary" in p.fragments and "## 数据边界" in p.system_text()
    assert p.skill_id.startswith("demo_prompt@0.1.0#") and len(p.content_hash) == 64
    text = p.render("main", target={"ref": "r", "type": "table", "caption": "Table 1"}, inputs={})
    assert "Table 1" in text
    py = load_package(FIX / "demo_python", known_tools=TOOLS, known_tasks=TASKS)
    assert py.ok, py.errors
    assert py.resources_index == {"notes.md": ["统计规则", "已知限制"]}
    assert "空白切分" in py.read_resource("notes.md#统计规则")
    assert "中文分词" in py.read_resource("已知限制")
    assert any(w.code == "W_L1_SECTION_MISSING" for w in py.warnings) is False
    with pytest.raises(KeyError):
        py.read_resource("nope")


def test_content_hash_changes_with_content(tmp_path: Path) -> None:
    d = tmp_path / "demo_prompt"
    shutil.copytree(FIX / "demo_prompt", d)
    h1 = directory_hash(d)
    (d / "prompts" / "task.md").write_text("changed {{ target.ref }}", encoding="utf-8")
    assert directory_hash(d) != h1
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "x.pyc").write_bytes(b"x")
    assert directory_hash(d) == directory_hash(d)


def test_prompt_json_keeps_unicode_readable_and_roundtrips():
    from drugpaper.skills.loader import LoadedSkill

    skill = LoadedSkill(path=Path("."), scope="builtin", prompts={"main": "{{ inputs | tojson }}"})
    value = {"说明": "母核与取代位点", "annotation": '<script> & "quoted"'}
    rendered = skill.render("main", inputs=value)
    assert "母核与取代位点" in rendered and "\\u6bcd" not in rendered
    assert json.loads(rendered) == value


def _mutate(tmp_path: Path, name: str, fn: Callable[[Path], None]) -> Path:
    d = tmp_path / name
    shutil.copytree(FIX / name, d)
    fn(d)
    return d


def _edit(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


CASES: list[tuple[str, str, Callable[[Path], None]]] = [
    ("E_MANIFEST_MISSING", "demo_prompt", lambda d: (d / "manifest.yaml").unlink()),
    (
        "E_MANIFEST_YAML",
        "demo_prompt",
        lambda d: (d / "manifest.yaml").write_text("name: [x", encoding="utf-8"),
    ),
    (
        "E_MANIFEST_SCHEMA",
        "demo_prompt",
        lambda d: _edit(d / "manifest.yaml", "kind: prompt", "kind: prompt\nbogus: 1"),
    ),
    ("E_MANIFEST_SCHEMA", "demo_prompt", lambda d: _edit(d / "manifest.yaml", "version: 0.1.0\n", "")),
    (
        "E_NAME_MISMATCH",
        "demo_prompt",
        lambda d: _edit(d / "manifest.yaml", "name: demo_prompt", "name: other_name"),
    ),
    ("E_SKILL_MD_MISSING", "demo_prompt", lambda d: (d / "SKILL.md").unlink()),
    (
        "E_FRONTMATTER",
        "demo_prompt",
        lambda d: (d / "SKILL.md").write_text("## 何时使用\nx", encoding="utf-8"),
    ),
    (
        "E_L0_BUDGET",
        "demo_prompt",
        lambda d: _edit(d / "SKILL.md", "测试用：给图表节点打一个标签。", "很长" * 40),
    ),
    ("E_L1_BUDGET", "demo_prompt", lambda d: _edit(d / "SKILL.md", "生产环境。", "生产环境。" * 120)),
    ("E_SCHEMA_FILE", "demo_prompt", lambda d: (d / "schemas" / "output.schema.json").unlink()),
    (
        "E_SCHEMA_INVALID",
        "demo_prompt",
        lambda d: (d / "schemas" / "output.schema.json").write_text("{bad", encoding="utf-8"),
    ),
    (
        "E_SCHEMA_INVALID",
        "demo_prompt",
        lambda d: (d / "schemas" / "output.schema.json").write_text(
            json.dumps({"type": "nope"}), encoding="utf-8"
        ),
    ),
    ("E_PROMPT_MISSING", "demo_prompt", lambda d: (d / "prompts" / "task.md").unlink()),
    (
        "E_PROMPT_COMPILE",
        "demo_prompt",
        lambda d: (d / "prompts" / "task.md").write_text("{% if x %}", encoding="utf-8"),
    ),
    (
        "E_INCLUDE_MISSING",
        "demo_prompt",
        lambda d: _edit(d / "manifest.yaml", "common/data_boundary", "common/nope"),
    ),
    (
        "E_TASK_UNKNOWN",
        "demo_prompt",
        lambda d: _edit(d / "manifest.yaml", "main: default", "main: no_such_task"),
    ),
    (
        "E_TASK_REQUIRED",
        "demo_prompt",
        lambda d: _edit(d / "manifest.yaml", "model_tasks:\n  main: default\n", ""),
    ),
    (
        "E_PROMPT_REQUIRED",
        "demo_prompt",
        lambda d: _edit(d / "manifest.yaml", "  main: prompts/task.md\n", ""),
    ),
    (
        "E_TOOL_UNKNOWN",
        "demo_python",
        lambda d: _edit(d / "manifest.yaml", "tools: [get_node]", "tools: [get_node, rm_rf]"),
    ),
    ("E_ENTRY_MISSING", "demo_python", lambda d: (d / "scripts" / "main.py").unlink()),
    (
        "E_SCOPE_MISMATCH",
        "demo_python",
        lambda d: _edit(d / "manifest.yaml", "kind: python", "kind: python\nscope: user"),
    ),
]


@pytest.mark.parametrize(("code", "base", "mutate"), CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_invalid_packages_report_error_code(
    tmp_path: Path, code: str, base: str, mutate: Callable[[Path], None]
) -> None:
    d = _mutate(tmp_path, base, mutate)
    p = load_package(d, known_tools=TOOLS, known_tasks=TASKS)
    assert not p.ok
    assert code in p.error_codes(), p.errors


def test_warnings(tmp_path: Path) -> None:
    def strip(d: Path) -> None:
        _edit(d / "SKILL.md", "## 何时不用\n生产环境。\n", "")
        shutil.rmtree(d / "tests")

    p = load_package(_mutate(tmp_path, "demo_prompt", strip), known_tools=TOOLS, known_tasks=TASKS)
    assert p.ok
    assert {w.code for w in p.warnings} == {"W_L1_SECTION_MISSING", "W_NO_TESTS"}


def test_split_sections() -> None:
    assert split_sections("# T\n## A\nx\n### B\ny\n") == {"A": "x", "B": "y"}


def test_resource_path_is_indexed_and_respects_token_limit() -> None:
    from drugpaper.capabilities.tokens import estimate_tokens

    p = load_package(FIX / "demo_prompt")
    with pytest.raises(KeyError):
        p.read_resource("../../pyproject.toml#unknown")
    # 包目录中的非 reference 文件不能借 L3 读取。
    with pytest.raises(KeyError):
        p.read_resource("../manifest.yaml#name")
    for path, sections in p.resources_index.items():
        if sections:
            assert estimate_tokens(p.read_resource(path + "#" + sections[0], max_tokens=10)) <= 10


def test_package_relative_reference_path_matches_disclosed_card():
    skill = load_package(FIX / "demo_python")
    assert skill.read_resource("references/notes.md#统计规则") == skill.read_resource("notes.md#统计规则")
    with pytest.raises(KeyError):
        skill.read_resource("references/../../pyproject.toml#unknown")
