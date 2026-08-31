import csv
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

from app.core.errors import ValidationError
from app.schemas.dataset import (
    ColumnProfile,
    DatasetFileProfile,
    DatasetProfile,
    DateRange,
    NumericStatistics,
    ProfileError,
    SheetProfile,
    ValueFrequency,
)
from app.services.workspace import PathResolver

SAMPLE_ROWS = 10
SAMPLE_COLUMNS = 50
SAMPLE_VALUE_LENGTH = 200
TOP_VALUE_LIMIT = 10


class DatasetProfiler:
    def __init__(self, resolver: PathResolver) -> None:
        self.resolver = resolver

    def profile_project(self, project_id: str) -> DatasetProfile:
        input_directory = self.resolver.resolve(project_id, "input")
        files: list[DatasetFileProfile] = []
        errors: list[ProfileError] = []
        for path in sorted(input_directory.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
                continue
            relative_path = path.relative_to(self.resolver.project_root(project_id)).as_posix()
            try:
                files.append(self.profile_file(path, relative_path))
            except Exception as exc:
                # Spreadsheet parsers expose several library-specific exceptions for corrupt files.
                errors.append(ProfileError(path=relative_path, message=self._safe_error(path, exc)))
        profile = DatasetProfile(generated_at=datetime.now(UTC), files=files, errors=errors)
        target = self.resolver.resolve(project_id, "context/dataset_profile.json")
        target.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        return profile

    def profile_file(self, path: Path, relative_path: str) -> DatasetFileProfile:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            frames = {"CSV": self._read_csv(path)}
        elif suffix in {".xlsx", ".xls"}:
            engine = "xlrd" if suffix == ".xls" else "openpyxl"
            frames = pd.read_excel(path, sheet_name=None, engine=engine)
        else:
            raise ValidationError("Unsupported dataset type")
        sheets = [self._profile_frame(str(name), frame) for name, frame in frames.items()]
        return DatasetFileProfile(path=relative_path, filename=path.name, sheets=sheets)

    def _read_csv(self, path: Path) -> pd.DataFrame:
        encoding = self._detect_encoding(path)
        sample = path.read_bytes()[:65_536].decode(encoding, errors="strict")
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            separator = dialect.delimiter
        except csv.Error:
            separator = ","
        return pd.read_csv(path, encoding=encoding, sep=separator)

    @staticmethod
    def _detect_encoding(path: Path) -> str:
        sample = path.read_bytes()[:65_536]
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                sample.decode(encoding, errors="strict")
                return encoding
            except UnicodeDecodeError:
                continue
        raise ValueError("Unsupported CSV encoding")

    def _profile_frame(self, name: str, frame: pd.DataFrame) -> SheetProfile:
        normalized = frame.copy()
        normalized.columns = [str(column)[:200] for column in normalized.columns]
        columns = [
            self._profile_column(column, normalized[column]) for column in normalized.columns
        ]
        sample_columns = list(normalized.columns[:SAMPLE_COLUMNS])
        sample = [
            {column: self._json_value(value) for column, value in row.items()}
            for row in normalized[sample_columns].head(SAMPLE_ROWS).to_dict(orient="records")
        ]
        return SheetProfile(
            name=name[:200],
            row_count=len(normalized),
            column_count=len(normalized.columns),
            columns=columns,
            sample=sample,
        )

    def _profile_column(self, name: str, series: pd.Series) -> ColumnProfile:
        missing_rate = float(series.isna().mean()) if len(series) else 0.0
        unique_count = int(series.nunique(dropna=True))
        if is_numeric_dtype(series):
            clean = pd.to_numeric(series, errors="coerce").dropna()
            stats = NumericStatistics(
                minimum=self._finite_float(clean.min()) if len(clean) else None,
                maximum=self._finite_float(clean.max()) if len(clean) else None,
                mean=self._finite_float(clean.mean()) if len(clean) else None,
                median=self._finite_float(clean.median()) if len(clean) else None,
            )
            return ColumnProfile(
                name=name,
                dtype=str(series.dtype),
                missing_rate=missing_rate,
                unique_count=unique_count,
                numeric_statistics=stats,
            )

        dates = self._date_values(name, series)
        date_range = None
        if dates is not None and not dates.empty:
            date_range = DateRange(minimum=dates.min().isoformat(), maximum=dates.max().isoformat())
        frequencies = (
            series.dropna()
            .astype(str)
            .str.slice(0, SAMPLE_VALUE_LENGTH)
            .value_counts()
            .head(TOP_VALUE_LIMIT)
        )
        top_values = [
            ValueFrequency(value=str(value), count=int(count))
            for value, count in frequencies.items()
        ]
        return ColumnProfile(
            name=name,
            dtype="datetime" if date_range else str(series.dtype),
            missing_rate=missing_rate,
            unique_count=unique_count,
            top_values=top_values,
            date_range=date_range,
        )

    @staticmethod
    def _date_values(name: str, series: pd.Series) -> pd.Series | None:
        if is_datetime64_any_dtype(series):
            return pd.to_datetime(series.dropna(), errors="coerce").dropna()
        hint = name.lower()
        if not any(token in hint for token in ("date", "time", "日期", "时间")):
            return None
        converted = pd.to_datetime(series.dropna(), errors="coerce")
        if len(converted) and float(converted.notna().mean()) >= 0.8:
            return converted.dropna()
        return None

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        result = float(value)
        return result if math.isfinite(result) else None

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value is None or (not isinstance(value, (str, bytes)) and pd.isna(value)):
            return None
        if isinstance(value, (pd.Timestamp, datetime, date)):
            return value.isoformat()
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, str):
            return value[:SAMPLE_VALUE_LENGTH]
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)[:SAMPLE_VALUE_LENGTH]

    @staticmethod
    def _safe_error(path: Path, error: Exception) -> str:
        if isinstance(error, ImportError):
            return f"{path.suffix.lower()} reader dependency is unavailable"
        return f"{path.name} cannot be read as a valid dataset"
