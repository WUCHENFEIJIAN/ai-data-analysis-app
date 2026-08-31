from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NumericStatistics(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    median: float | None = None


class ValueFrequency(BaseModel):
    value: str
    count: int


class DateRange(BaseModel):
    minimum: str
    maximum: str


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    missing_rate: float = Field(ge=0, le=1)
    unique_count: int = Field(ge=0)
    numeric_statistics: NumericStatistics | None = None
    top_values: list[ValueFrequency] | None = None
    date_range: DateRange | None = None


class SheetProfile(BaseModel):
    name: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[ColumnProfile]
    sample: list[dict[str, Any]]


class DatasetFileProfile(BaseModel):
    path: str
    filename: str
    sheets: list[SheetProfile]


class ProfileError(BaseModel):
    path: str
    message: str


class DatasetProfile(BaseModel):
    generated_at: datetime
    files: list[DatasetFileProfile]
    errors: list[ProfileError] = Field(default_factory=list)
