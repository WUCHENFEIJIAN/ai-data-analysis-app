"""Turn a validated ReportEditorSpec into the existing renderer ReportSpec."""

from __future__ import annotations

from app.services.metric_contract import (
    MetricDefinition,
    metric_display_scale,
    metric_display_unit,
    metric_ratio_value_basis,
)
from app.services.presentation_metadata import PresentationMetadata
from app.services.report_editor_spec import (
    ReportEditorCalloutBlock,
    ReportEditorChartBlock,
    ReportEditorKpiGridBlock,
    ReportEditorNarrativeBlock,
    ReportEditorRecommendationsBlock,
    ReportEditorSpec,
    ReportEditorTableBlock,
    ReportEditorVisualGroupBlock,
)
from app.services.report_inputs import ArtifactEntry, ReportInputs
from app.services.report_metric_fidelity import metric_definition_for_field
from app.services.report_semantics import display_label_for, narratives_are_duplicate
from app.services.report_spec import (
    CalloutBlock,
    ChartBlock,
    ChartSpec,
    KpiGridBlock,
    KpiSpec,
    NarrativeBlock,
    RecommendationBlock,
    RecommendationItemSpec,
    ReportSpec,
    SectionSpec,
    SeriesSpec,
    SourceSpec,
    TableBlock,
    TableColumnSpec,
    VisualGroupBlock,
)
from app.services.report_validator import _analytical_role_for_blocks


class ReportEditorAssembler:
    def assemble(
        self,
        draft: ReportEditorSpec,
        inputs: ReportInputs,
        planner_mode: str = "llm",
    ) -> ReportSpec:
        metrics = {item.metric_id: item for item in inputs.metrics}
        by_ref = self._artifact_index(inputs.catalog)
        sources: dict[str, SourceSpec] = {}
        kpis: list[KpiSpec] = []
        kpi_decisions = {item.metric_ref: item for item in draft.kpis}

        sections: list[SectionSpec] = []
        selected_paths: list[str] = []
        recommendation_items: list[RecommendationItemSpec] = []
        for section_index, section in enumerate(draft.sections, start=1):
            blocks: list[object] = []
            lead_text: str | None = None
            first_narrative = next(
                (item for item in section.blocks if item.type == "narrative"),
                None,
            )
            if section.lead and not (
                first_narrative is not None
                and narratives_are_duplicate(section.lead, getattr(first_narrative, "text", None))
            ):
                blocks.append(
                    NarrativeBlock(
                        type="narrative",
                        text=section.lead,
                        purpose="section lead",
                        finding_ids=list(section.finding_refs),
                        claim_ids=[],
                        display_role="lead",
                    )
                )
            lead_text = section.lead
            chart_index = 0
            table_index = 0
            for block in section.blocks:
                if isinstance(block, ReportEditorNarrativeBlock):
                    display_role = block.display_role
                    if display_role == "lead":
                        if lead_text is None:
                            lead_text = block.text
                        elif narratives_are_duplicate(lead_text, block.text):
                            continue
                        else:
                            display_role = "supporting_narrative"
                    blocks.append(
                        NarrativeBlock(
                            type="narrative",
                            text=block.text,
                            purpose=block.purpose or section.title,
                            finding_ids=list(section.finding_refs),
                            claim_ids=list(block.claim_ids),
                            composite_insight_ids=[],
                            display_role=display_role,
                            related_block_id=block.related_block_id,
                            related_block_ids=list(block.related_block_ids),
                            metric_refs=list(block.metric_refs),
                        )
                    )
                elif isinstance(block, ReportEditorKpiGridBlock):
                    kpi_ids = []
                    for metric_ref in block.metric_refs:
                        definition = metrics[metric_ref]
                        kpi_id = self._kpi_id(metric_ref, len(kpis) + 1)
                        if not any(item.id == kpi_id for item in kpis):
                            decision = kpi_decisions.get(metric_ref)
                            kpis.append(
                                self._kpi(
                                    kpi_id,
                                    decision.display_label if decision else definition.label,
                                    decision.purpose if decision else definition.definition,
                                    definition,
                                )
                            )
                        kpi_ids.append(kpi_id)
                        entry = by_ref.get(definition.source_artifact)
                        if entry is not None:
                            sources[entry.id] = self._source(entry, "evidence_only")
                    blocks.append(
                        KpiGridBlock(
                            type="kpi_grid",
                            kpi_ids=kpi_ids,
                            presentation_role=block.presentation_role,
                        )
                    )
                elif isinstance(block, ReportEditorChartBlock):
                    chart_index += 1
                    entry = by_ref[block.data_ref]
                    sources[entry.id] = self._source(entry, "visual_source")
                    selected_paths.append(entry.path)
                    blocks.append(
                        self._chart(
                            section, block, entry, metrics, inputs, section_index, chart_index
                        )
                    )
                elif isinstance(block, ReportEditorTableBlock):
                    table_index += 1
                    entry = by_ref[block.data_ref]
                    sources[entry.id] = self._source(entry, "summary_table")
                    selected_paths.append(entry.path)
                    blocks.append(
                        self._table(
                            section, block, entry, metrics, inputs, section_index, table_index
                        )
                    )
                elif isinstance(block, ReportEditorVisualGroupBlock):
                    grouped = []
                    for item in block.items:
                        if isinstance(item, ReportEditorChartBlock):
                            chart_index += 1
                            entry = by_ref[item.data_ref]
                            sources[entry.id] = self._source(entry, "visual_source")
                            selected_paths.append(entry.path)
                            grouped.append(
                                self._chart(
                                    section,
                                    item,
                                    entry,
                                    metrics,
                                    inputs,
                                    section_index,
                                    chart_index,
                                )
                            )
                        elif isinstance(item, ReportEditorTableBlock):
                            table_index += 1
                            entry = by_ref[item.data_ref]
                            sources[entry.id] = self._source(entry, "summary_table")
                            selected_paths.append(entry.path)
                            grouped.append(
                                self._table(
                                    section,
                                    item,
                                    entry,
                                    metrics,
                                    inputs,
                                    section_index,
                                    table_index,
                                )
                            )
                    blocks.append(
                        VisualGroupBlock(
                            type="visual_group",
                            layout=block.layout,
                            items=grouped,
                        )
                    )
                elif isinstance(block, ReportEditorCalloutBlock):
                    blocks.append(
                        CalloutBlock(
                            type="callout",
                            tone=block.tone,
                            title=block.title,
                            text=block.text,
                        )
                    )
                elif isinstance(block, ReportEditorRecommendationsBlock):
                    recommendation_items.extend(
                        RecommendationItemSpec(
                            id=f"rec_pending_{len(recommendation_items) + item_index}",
                            text=item.text,
                            priority=item.priority,
                            source_finding_ids=list(item.source_finding_ids),
                            source_claim_ids=list(item.source_claim_ids),
                        )
                        for item_index, item in enumerate(block.items, start=1)
                    )
            if blocks:
                sections.append(
                    SectionSpec(
                        id=f"section_{section_index}",
                        title=section.title,
                        finding_ids=list(section.finding_refs),
                        claim_ids=list(section.claim_ids),
                        narrative_role="context",
                        visual_strategy=_assembled_section_role(section.section_role, blocks),
                        layout=section.layout,
                        blocks=blocks,
                    )
                )

        if recommendation_items:
            recommendation_section_index = len(sections) + 1
            recommendation_items = [
                item.model_copy(update={"id": f"rec_{recommendation_section_index}_{index}"})
                for index, item in enumerate(recommendation_items, start=1)
            ]
            sections.append(
                SectionSpec(
                    id=f"section_{recommendation_section_index}",
                    title="行动建议",
                    narrative_role="context",
                    visual_strategy="narrative_led",
                    layout="flow",
                    blocks=[
                        RecommendationBlock(
                            type="recommendations",
                            items=recommendation_items,
                        )
                    ],
                )
            )

        return ReportSpec(
            schema_version="3.0",
            locale="zh-CN",
            analysis_topic=inputs.analysis_topic,
            title=draft.headline,
            subtitle=inputs.subtitle,
            theme="editorial",
            executive_summary=draft.summary,
            sources=list(sources.values()),
            kpis=kpis,
            sections=sections,
            provenance={
                "planner_mode": planner_mode,
                "findings_path": "analysis/findings.json",
                "source_artifacts": list(dict.fromkeys(selected_paths)),
            },
        )

    @staticmethod
    def _artifact_index(catalog: list[ArtifactEntry]) -> dict[str, ArtifactEntry]:
        index: dict[str, ArtifactEntry] = {}
        for entry in catalog:
            index[entry.path] = entry
            index[entry.id] = entry
        return index

    @staticmethod
    def _source(entry: ArtifactEntry, usage: str) -> SourceSpec:
        return SourceSpec(
            id=entry.id if entry.id[0].isalpha() else f"src_{entry.id}",
            artifact_path=entry.path,
            kind=entry.kind,
            sha256=entry.sha256,
            media_type=entry.media_type,
            usage=usage,
        )

    @staticmethod
    def _kpi_id(metric_ref: str, index: int) -> str:
        cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in metric_ref)
        if cleaned and cleaned[0].isalpha():
            return cleaned[:80]
        return f"kpi_{index}"

    @staticmethod
    def _kpi(kpi_id: str, label: str, purpose: str, definition: MetricDefinition) -> KpiSpec:
        meta = PresentationMetadata.from_metric(definition)
        return KpiSpec(
            id=kpi_id,
            label=label,
            canonical_name=definition.label,
            display_label=label,
            definition_note=definition.definition,
            metric=definition.metric_id,
            value_ref=None,
            format=(
                meta.format_name
                if meta.format_name in {"number", "integer", "currency", "percent"}
                else "number"
            ),
            decimals=meta.decimals,
            unit=meta.display_unit or None,
            scale=meta.display_scale,
            purpose=purpose,
            role="overview",
            presentation_roles=["overview"],
            metric_definition=definition,
        )

    @staticmethod
    def _chart(
        section,
        visual,
        entry: ArtifactEntry,
        metrics,
        inputs: ReportInputs,
        section_index: int,
        visual_index: int,
    ):
        records_path = list((entry.structure or {}).get("records_path") or [])
        series = []
        for field_name in visual.series:
            definition = metric_definition_for_field(entry.path, field_name, inputs)
            meta = PresentationMetadata.from_metric(definition) if definition else None
            series.append(
                SeriesSpec(
                    field=field_name,
                    label=definition.label if definition else field_name,
                    metric=definition.metric_id if definition else field_name,
                    format=(
                        meta.format_name
                        if meta and meta.format_name in {"number", "integer", "currency", "percent"}
                        else "number"
                    ),
                    decimals=meta.decimals if meta else 0,
                    unit=meta.display_unit if meta and meta.display_unit else None,
                    scale=meta.display_scale if meta else 1,
                    metric_definition=definition,
                    presentation_usable=bool(meta and meta.usable),
                )
            )
        source_id = entry.id if entry.id[0].isalpha() else f"src_{entry.id}"
        return ChartBlock(
            type="chart",
            chart=ChartSpec(
                id=f"chart_{section_index}_{visual_index}",
                chart_type=visual.chart_type,
                title=visual.title,
                finding_ids=list(section.finding_refs),
                purpose=visual.purpose,
                source_id=source_id,
                records_path=records_path,
                x_field=visual.x_field,
                x_semantic=_chart_x_semantic(entry, visual.x_field),
                x_display_label=next(
                    (
                        item.get("display_label")
                        for item in (entry.structure or {}).get("columns", [])
                        if item.get("name") == visual.x_field and item.get("display_label")
                    ),
                    display_label_for(visual.x_field),
                ),
                series=series,
                source_caption="数据来源：分析结果汇总",
            ),
        )

    @staticmethod
    def _table(
        section,
        table,
        entry: ArtifactEntry,
        metrics,
        inputs: ReportInputs,
        section_index: int,
        table_index: int,
    ):
        records_path = list((entry.structure or {}).get("records_path") or [])
        columns = []
        field_meta = {
            item.get("name"): item
            for item in (entry.structure or {}).get("columns", [])
            if item.get("name")
        }
        for field_name in table.columns:
            definition = metric_definition_for_field(entry.path, field_name, inputs)
            meta = field_meta.get(field_name) or {}
            semantic = meta.get("semantic_type") or "text"
            if definition is not None:
                if definition.unit_family == "percentage":
                    semantic = (
                        "percentage_fraction"
                        if metric_ratio_value_basis(definition) == "fraction"
                        else "percentage_points"
                    )
                elif definition.unit_family == "currency":
                    semantic = "currency"
                elif definition.unit_family == "count" or definition.semantic_type == "count":
                    semantic = "integer"
            format_name = {
                "percentage_fraction": "percent",
                "percentage_points": "percent",
                "currency": "currency",
                "integer": "integer",
                "decimal": "number",
                "identifier": "text",
            }.get(semantic, "number" if meta.get("type") == "number" else "text")
            columns.append(
                TableColumnSpec(
                    field=field_name,
                    label=(
                        meta.get("display_label")
                        or (definition.label if definition else None)
                        or display_label_for(field_name)
                    ),
                    format=format_name,
                    decimals=(
                        2
                        if semantic.startswith("percentage")
                        else (1 if format_name in {"number", "currency"} else 0)
                    ),
                    unit=(metric_display_unit(definition) or None) if definition else None,
                    scale=(metric_display_scale(definition) if definition is not None else 1),
                    metric=definition.metric_id if definition else None,
                    metric_definition=definition,
                    semantic_type=semantic,
                )
            )
        source_id = entry.id if entry.id[0].isalpha() else f"src_{entry.id}"
        return TableBlock(
            type="table",
            id=f"table_{section_index}_{table_index}",
            source_id=source_id,
            title=table.title,
            finding_ids=list(section.finding_refs),
            purpose=table.purpose,
            usage="summary_table",
            records_path=records_path,
            columns=columns,
        )


def _assembled_section_role(declared: str | None, blocks: list[object]) -> str:
    if declared != "context_only":
        return declared or "balanced"
    return _analytical_role_for_blocks(blocks, assembled=True) or "context_only"


def _chart_x_semantic(entry: ArtifactEntry, field: str) -> str:
    for column in (entry.structure or {}).get("columns", []):
        if column.get("name") == field:
            semantic = str(column.get("temporal_semantic") or column.get("semantic_type") or "")
            if semantic in {"date", "datetime", "month", "year", "temporal"}:
                return semantic
            break
    return "category"
