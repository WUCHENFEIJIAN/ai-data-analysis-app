"""Generic report column semantics. Rules use value/metadata types, never field names."""

from __future__ import annotations

import re
from typing import Any, Literal

ColumnSemantic = Literal[
    "text",
    "identifier",
    "integer",
    "decimal",
    "currency",
    "percentage_fraction",
    "percentage_points",
    "date",
    "datetime",
]

_TIME_RE = re.compile(
    r"^(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::\d{2})?)?)?)?$"
)


def parse_time(value: Any) -> tuple[int, int, int, bool] | None:
    text = str(value or "").strip()
    match = _TIME_RE.match(text)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2) or 1),
        int(match.group(3) or 1),
        bool(match.group(4)),
    )


def display_label_for(field: str) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in field):
        return field
    cleaned = re.sub(r"[_\-]+", " ", field).strip()
    if not cleaned:
        return field
    if "_" not in field and "-" not in field:
        return field
    return cleaned.title()


def classify_column_values(values: list[Any]) -> ColumnSemantic:
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "text"
    parsed = [parse_time(value) for value in present]
    if all(item is not None for item in parsed):
        return "datetime" if any(item[3] for item in parsed) else "date"
    numbers: list[float] = []
    for value in present:
        try:
            numbers.append(float(str(value).replace(",", "").rstrip("%")))
        except (TypeError, ValueError):
            return "text"
    as_ints = all(number.is_integer() for number in numbers)
    unique_ratio = len(set(numbers)) / len(numbers)
    if as_ints and unique_ratio >= 0.9:
        digits = [len(str(abs(int(number)))) for number in numbers]
        if digits and min(digits) >= 6:
            return "identifier"
    if all(0 <= number <= 1 for number in numbers) and any(
        number not in {0.0, 1.0} for number in numbers
    ):
        return "percentage_fraction"
    if as_ints:
        return "integer"
    return "decimal"


def _as_number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return None


def _compact_percent(number: float) -> str:
    return f"{number:.2f}".rstrip("0").rstrip(".") + "%"


def format_table_value(
    value: Any,
    semantic_type: str,
    *,
    format_name: str = "text",
    decimals: int = 0,
    unit: str | None = None,
    scale: int | float = 1,
) -> str:
    if value in (None, ""):
        return ""
    if semantic_type == "identifier":
        text = str(value).strip()
        number = _as_number(text)
        if number is not None and number.is_integer():
            return str(int(number))
        if re.fullmatch(r"-?\d+\.0+", text):
            return text.split(".", 1)[0]
        return text
    if semantic_type == "percentage_fraction":
        number = _as_number(value)
        if number is None:
            return str(value)
        return _compact_percent(number * 100)
    if semantic_type == "percentage_points":
        number = _as_number(value)
        if number is None:
            return str(value)
        return _compact_percent(number)
    if semantic_type in {"date", "datetime"}:
        parsed = parse_time(value)
        if parsed is None:
            return str(value)
        year, month, day, has_time = parsed
        if semantic_type == "datetime" and has_time:
            return f"{year}/{month}/{day}"
        return f"{month}/{day}"
    if format_name == "text" and semantic_type == "text":
        return str(value)
    number = _as_number(value)
    if number is None:
        return str(value)
    scaled = number / (scale or 1)
    if semantic_type == "integer" or format_name == "integer":
        rendered = f"{int(round(scaled)):,}"
    else:
        rendered = f"{scaled:,.{decimals}f}"
        if semantic_type in {"decimal", "currency"} or format_name in {"number", "currency"}:
            rendered = rendered.rstrip("0").rstrip(".") if decimals else rendered
    suffix = unit or ("" if format_name != "percent" else "%")
    if format_name == "percent" and "%" not in suffix:
        suffix += "%"
    return rendered + suffix


def narratives_are_duplicate(left: str | None, right: str | None) -> bool:
    """Lightweight consecutive-duplicate check. Not NLP."""

    normalized_left = re.sub(r"\s+", "", left or "")
    normalized_right = re.sub(r"\s+", "", right or "")
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    shorter, longer = (
        (normalized_left, normalized_right)
        if len(normalized_left) <= len(normalized_right)
        else (normalized_right, normalized_left)
    )
    return len(shorter) >= 24 and shorter in longer
