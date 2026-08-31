from pathlib import Path

import pytest

from app.core.errors import AppError
from app.skills.loader import SkillLoader, SkillStage


def skill_root() -> Path:
    return Path(__file__).parents[2] / "DAskill" / "data-analysis"


def test_skill_loader_selects_only_stage_content() -> None:
    loader = SkillLoader(skill_root())

    understand = loader.load(SkillStage.UNDERSTAND)
    analysis = loader.load(SkillStage.ANALYSIS)
    report = loader.load(SkillStage.REPORT)

    assert "## 指导原则" in understand
    assert "Reference: workflows.md" not in understand
    assert "Reference: workflows.md" in analysis
    assert "Reference: report-style-gallery.md" not in analysis
    assert "Reference: report-style-gallery.md" in report
    assert "Reference: html-templates.md" in report


def test_ad_reference_is_loaded_only_when_relevant() -> None:
    loader = SkillLoader(skill_root())
    assert "投放/广告分析领域知识" not in loader.load(SkillStage.ANALYSIS)
    assert "投放/广告分析领域知识" in loader.load(SkillStage.ANALYSIS, include_ad_analytics=True)


def test_missing_skill_and_reference_are_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(AppError, match="unavailable") as missing_skill:
        SkillLoader(tmp_path).load(SkillStage.UNDERSTAND)
    assert missing_skill.value.code == "skill_missing"

    root = tmp_path / "skill"
    root.mkdir()
    root.joinpath("SKILL.md").write_text(
        skill_root().joinpath("SKILL.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(AppError, match="workflows.md") as missing_reference:
        SkillLoader(root).load(SkillStage.ANALYSIS)
    assert missing_reference.value.code == "skill_reference_missing"
