"""Bounded, deterministic schema inspection for generated data Artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.services.report_semantics import classify_column_values, display_label_for


class ArtifactSchemaInspector:
    MAX_COLUMNS = 60
    MAX_SAMPLE_ROWS = 100

    def inspect(self, path: Path) -> dict[str, Any] | None:
        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                return self._csv(path)
            if suffix == ".json":
                return self._json(path)
            if suffix == ".parquet":
                return self._parquet(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError):
            return {"record_kind": "unreadable"}
        return None

    def _csv(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])[: self.MAX_COLUMNS]
            sample: list[dict[str, Any]] = []
            row_count = 0
            for row in reader:
                row_count += 1
                if len(sample) < self.MAX_SAMPLE_ROWS:
                    sample.append(row)
        return self._table_structure(columns, sample, row_count)

    def _json(self, path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        records_path: list[str] = []
        records = value
        if isinstance(value, dict):
            for key in ("records", "data"):
                if isinstance(value.get(key), list):
                    records = value[key]
                    records_path = [key]
                    break
        if isinstance(records, list) and (not records or isinstance(records[0], dict)):
            columns = list(records[0])[: self.MAX_COLUMNS] if records else []
            structure = self._table_structure(
                columns, records[: self.MAX_SAMPLE_ROWS], len(records)
            )
            structure["records_path"] = records_path
            if isinstance(value, dict):
                structure["keys"] = list(value)[: self.MAX_COLUMNS]
                structure["value_paths"] = _scalar_paths(value)
            return structure
        if isinstance(value, dict):
            return {
                "record_kind": "object",
                "keys": list(value)[: self.MAX_COLUMNS],
                "value_paths": _scalar_paths(value),
            }
        return {"record_kind": type(value).__name__}

    def _parquet(self, path: Path) -> dict[str, Any]:
        try:
            import pyarrow.parquet as parquet
        except ImportError:
            return {"record_kind": "unsupported", "format": "parquet"}
        table = parquet.read_table(path)
        columns = [
            {"name": field.name, "dtype": str(field.type), "type": str(field.type)}
            for field in table.schema[: self.MAX_COLUMNS]
        ]
        return {
            "record_kind": "table",
            "columns": columns,
            "fields": [{"name": item["name"], "type": item["type"]} for item in columns],
            "row_count": table.num_rows,
        }

    @staticmethod
    def _table_structure(
        columns: list[str], sample: list[dict[str, Any]], row_count: int
    ) -> dict[str, Any]:
        definitions = []
        for column in columns:
            values = [row.get(column) for row in sample]
            definitions.append(
                {
                    "name": column,
                    "dtype": _sample_type(values),
                    "type": _sample_type(values),
                    "semantic_type": classify_column_values(values),
                    "display_label": display_label_for(column),
                }
            )
        return {
            "record_kind": "table",
            "columns": definitions,
            "fields": [{"name": item["name"], "type": item["type"]} for item in definitions],
            "row_count": row_count,
        }


def _sample_type(values: list[Any]) -> str:
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "unknown"
    lowered = {str(value).strip().lower() for value in present}
    if lowered <= {"true", "false"}:
        return "boolean"
    try:
        for value in present:
            float(str(value).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return "string"
    return "number"


def _scalar_paths(
    value: Any, prefix: tuple[str | int, ...] = (), depth: int = 0
) -> list[list[Any]]:
    if depth > 3:
        return []
    if isinstance(value, dict):
        paths: list[list[Any]] = []
        for key, item in list(value.items())[:60]:
            paths.extend(_scalar_paths(item, (*prefix, key), depth + 1))
            if len(paths) >= 120:
                break
        return paths[:120]
    if isinstance(value, list):
        return []
    return [list(prefix)]
