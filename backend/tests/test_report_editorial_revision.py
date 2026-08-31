"""One-shot local editorial revision. No repair loop."""

from __future__ import annotations

from app.llm.mock import MockLLMProvider
from app.services.report_editor_spec import (
    ReportEditorRevision,
    ReportEditorSpec,
)
from app.services.report_editorial_revision import merge_revision
from app.services.reports import ReportService
from app.skills.loader import SkillLoader
from tests.test_report_editor_pipeline import _editor_spec, prepare_editor_project


def _chart_block() -> dict:
    return {
        "type": "chart",
        "data_ref": "data/summary.csv",
        "chart_type": "bar",
        "x_field": "region",
        "series": ["sales"],
        "title": "Category comparison",
        "purpose": "Show the measured gap",
    }


def _overlapping_draft() -> dict:
    draft = _editor_spec()
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "First supporting restates the same conclusion.",
            "display_role": "supporting_narrative",
            "purpose": "Repeat once",
        },
        _chart_block(),
        {
            "type": "narrative",
            "text": "Second supporting restates the same conclusion.",
            "display_role": "supporting_narrative",
            "purpose": "Repeat twice",
        },
    ]
    return draft


def _revised_section() -> dict:
    return {
        "title": "One category holds the measured total",
        "finding_refs": ["finding_1"],
        "layout": "flow",
        "blocks": [
            {
                "type": "narrative",
                "text": "One category still accounts for the measured total.",
                "display_role": "lead",
                "purpose": "Primary judgment",
            },
            _chart_block(),
            {
                "type": "narrative",
                "text": "The bar gap shows concentration rather than a second independent story.",
                "display_role": "evidence_interpretation",
                "related_block_id": "data/summary.csv",
                "purpose": "Explain the chart",
            },
        ],
    }


async def _generate(client, settings, project, resolver, provider):
    with client.app.state.database.session() as session:
        return await ReportService(
            session, resolver, SkillLoader(settings.skill_root), provider
        ).generate(project["id"], "Analyze", "Category performance")


def test_merge_revision_restores_dropped_chart() -> None:
    original = ReportEditorSpec.model_validate(_overlapping_draft())
    revision = ReportEditorRevision.model_validate({"sections": [_revised_section()]})
    revision.sections[0] = revision.sections[0].model_copy(
        update={"blocks": [block for block in revision.sections[0].blocks if block.type != "chart"]}
    )
    merged = merge_revision(original, revision)
    types = [block.type for block in merged.sections[0].blocks]
    assert "chart" in types
    assert types.count("narrative") == 2


def test_revision_is_not_triggered_for_default_editor_spec() -> None:
    from app.services.report_editorial_lint import EditorialLint

    result = EditorialLint.check(ReportEditorSpec.model_validate(_editor_spec()))
    assert result.should_revise() is False


async def test_overlapping_section_is_revised_once(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    revised = _overlapping_draft()
    revised["sections"] = [_revised_section()]
    provider = MockLLMProvider([_overlapping_draft(), {"sections": [_revised_section()]}])
    path = await _generate(client, settings, project, resolver, provider)
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert [schema.__name__ for schema in provider.schemas] == [
        "ReportEditorSpec",
        "ReportEditorRevision",
    ]
    assert "First supporting restates the same conclusion." not in html
    assert "One category still accounts for the measured total." in html
    assert "Category comparison" in html
    assert len(provider.requests) == 2


async def test_revision_is_not_looped_when_warnings_remain(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    still_overlapping = {
        "title": "One category holds the measured total",
        "finding_refs": ["finding_1"],
        "layout": "flow",
        "blocks": [
            {
                "type": "narrative",
                "text": "Still repeating once.",
                "display_role": "supporting_narrative",
            },
            _chart_block(),
            {
                "type": "narrative",
                "text": "Still repeating twice.",
                "display_role": "supporting_narrative",
            },
        ],
    }
    provider = MockLLMProvider([_overlapping_draft(), {"sections": [still_overlapping]}])
    path = await _generate(client, settings, project, resolver, provider)
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert len(provider.schemas) == 2
    assert provider.schemas[1].__name__ == "ReportEditorRevision"
    assert "Still repeating once." in html
    assert html.startswith("<!doctype html>")


async def test_failed_revision_keeps_original_draft(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    provider = MockLLMProvider([_overlapping_draft()])
    path = await _generate(client, settings, project, resolver, provider)
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert "First supporting restates the same conclusion." in html
    assert html.startswith("<!doctype html>")
