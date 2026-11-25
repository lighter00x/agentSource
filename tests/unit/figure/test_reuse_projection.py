from drugpaper.api.workbench import required_stage


def test_semantic_scheme_validation_preserves_preprocessing_reuse(tmp_path):
    old, new = tmp_path / "old.py", tmp_path / "new.py"
    old.write_text(
        "def validate_package():\n schema_validate()\n if cls.semantic_model is not None:\n  old_semantics()\n return value\n"
    )
    new.write_text(
        "def validate_package():\n schema_validate()\n if cls.semantic_model is not None:\n  new_semantics()\n return value\n"
    )
    assert required_stage("drugpaper/skills/execution.py", before=old, after=new) == "structure"
    new.write_text(new.read_text().replace("schema_validate()", "changed_schema_validate()"))
    assert required_stage("drugpaper/skills/execution.py", before=old, after=new) == "classify"


def test_table_edits_allow_structuring_but_other_functions_invalidate(tmp_path):
    old, new = tmp_path / "old.py", tmp_path / "new.py"
    old.write_text("async def table_extract(): return 1\ndef panel_split(): return 1\n")
    new.write_text("async def table_extract(): return 2\ndef panel_split(): return 1\n")
    assert required_stage("drugpaper/skills/figure.py", before=old, after=new) == "structure"
    new.write_text("async def table_extract(): return 2\ndef panel_split(): return 2\n")
    assert required_stage("drugpaper/skills/figure.py", before=old, after=new) == "classify"


def test_prompt_projection_preserves_classifier_changes(tmp_path):
    old, new = tmp_path / "old.py", tmp_path / "new.py"
    old.write_text("def project_target(): return 1\n")
    new.write_text("def project_target(): return 1\nasync def document_evidence_images(): return []\n")
    assert required_stage("drugpaper/skills/prompt_materials.py", before=old, after=new) == "structure"
    new.write_text("def project_target(): return 2\nasync def document_evidence_images(): return []\n")
    assert required_stage("drugpaper/skills/prompt_materials.py", before=old, after=new) == "classify"


def test_executor_only_vision_branch_can_reuse_classification(tmp_path):
    old, new = tmp_path / "old.py", tmp_path / "new.py"
    old.write_text("def execute():\n if manifest.vision_structuring:\n  old_visual()\n return classify()\n")
    new.write_text("def execute():\n if manifest.vision_structuring:\n  new_visual()\n return classify()\n")
    assert required_stage("drugpaper/skills/execution.py", before=old, after=new) == "structure"
    new.write_text(
        "def execute():\n if manifest.vision_structuring:\n  new_visual()\n return different_classifier()\n"
    )
    assert required_stage("drugpaper/skills/execution.py", before=old, after=new) == "classify"


def test_extraction_budget_helper_preserves_midpoint_reuse_without_relaxing_classifier(tmp_path):
    old, new = tmp_path / "old.py", tmp_path / "new.py"
    old.write_text("def project_target(): return 1\n")
    new.write_text("def project_target(): return 1\ndef check_evidence_request(): return 1\n")
    assert required_stage("drugpaper/skills/prompt_materials.py", before=old, after=new) == "structure"
    new.write_text("def project_target(): return 2\ndef check_evidence_request(): return 1\n")
    assert required_stage("drugpaper/skills/prompt_materials.py", before=old, after=new) == "classify"


def test_only_backfill_edits_can_preserve_molecular_preprocessing(tmp_path):
    old, new = tmp_path / "old.py", tmp_path / "new.py"
    old.write_text("def mol_backfill(): return 1\ndef molecule_recognize(): return 1\n")
    new.write_text("def mol_backfill(): return 2\ndef molecule_recognize(): return 1\n")
    assert required_stage("drugpaper/skills/molecule.py", before=old, after=new) == "structure"
    new.write_text("def mol_backfill(): return 2\ndef molecule_recognize(): return 2\n")
    assert required_stage("drugpaper/skills/molecule.py", before=old, after=new) == "classify"
    assert required_stage("prompts/common/mol_instances.md") == "structure"
    assert required_stage("drugpaper/chem/instance_delivery.py") == "post"


def test_only_delivery_constraint_extension_does_not_invalidate_detection(tmp_path):
    from drugpaper.workbench.reuse import visual_extraction_only

    before = tmp_path / "before.py"
    after = tmp_path / "after.py"
    before.write_text(
        "class DeliveryIn:\n    data: dict\nasync def delivery(args):\n    return await asyncio.to_thread(deliver, args.data)\nasync def role(args):\n    return args.role\n"
    )
    after.write_text(
        "class DeliveryIn:\n    data: dict\n    blocked_instance_paths: list = []\nasync def delivery(args):\n    return await asyncio.to_thread(deliver, args.data, blocked_instance_paths=args.blocked_instance_paths)\nasync def role(args):\n    return args.role\n"
    )
    assert visual_extraction_only("drugpaper/tools/builtin/chem.py", before, after)
    after.write_text(after.read_text().replace("return args.role", 'return "changed"'))
    assert not visual_extraction_only("drugpaper/tools/builtin/chem.py", before, after)
