from enum import StrEnum
from pathlib import Path

from app.core.errors import AppError


class SkillStage(StrEnum):
    UNDERSTAND = "UNDERSTAND"
    ANALYSIS = "ANALYSIS"
    REPORT = "REPORT"


STAGE_SECTIONS = {
    SkillStage.UNDERSTAND: ["指导原则", "核心方法论：多专家深度分析", "分析哲学"],
    SkillStage.ANALYSIS: ["指导原则", "核心方法论：多专家深度分析", "分析哲学"],
    SkillStage.REPORT: ["HTML报告：基础布局契约", "设计哲学", "分析哲学"],
}
STAGE_REFERENCES = {
    SkillStage.UNDERSTAND: [],
    SkillStage.ANALYSIS: ["workflows.md"],
    SkillStage.REPORT: [
        "workflows.md",
        "report-style-gallery.md",
        "html-templates.md",
        "visual-design-system.md",
    ],
}


class SkillLoader:
    def __init__(self, skill_root: Path) -> None:
        self.skill_root = skill_root.resolve()

    def load(self, stage: SkillStage, include_ad_analytics: bool = False) -> str:
        main_path = self.skill_root / "SKILL.md"
        if not main_path.is_file():
            raise AppError("skill_missing", "Data analysis skill is unavailable", 500)
        content = main_path.read_text(encoding="utf-8")
        selected = [self._extract_section(content, heading) for heading in STAGE_SECTIONS[stage]]
        references = list(STAGE_REFERENCES[stage])
        if stage == SkillStage.ANALYSIS and include_ad_analytics:
            references.append("ad-analytics.md")
        for name in references:
            path = self.skill_root / "references" / name
            if not path.is_file():
                raise AppError(
                    "skill_reference_missing", f"Required skill reference is missing: {name}", 500
                )
            selected.append(f"## Reference: {name}\n\n{path.read_text(encoding='utf-8')}")
        return "\n\n".join(part for part in selected if part).strip()

    @staticmethod
    def _extract_section(content: str, heading: str) -> str:
        marker = f"## {heading}"
        start = content.find(marker)
        if start < 0:
            raise AppError(
                "skill_section_missing", f"Required skill section is missing: {heading}", 500
            )
        next_heading = content.find("\n## ", start + len(marker))
        return content[start : next_heading if next_heading >= 0 else len(content)].strip()
