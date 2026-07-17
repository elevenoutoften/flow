from __future__ import annotations

from datetime import datetime
import json


def cron_string_matches(expr: str, now: datetime) -> bool:
    """Match the supported subset of a standard 5-field cron expression.

    Day-of-week uses standard cron semantics: 0=Sunday, 1=Monday, …, 6=Saturday.
    7 is also accepted as Sunday.  ``datetime.weekday()`` returns Monday=0,
    so we convert to cron numbering (0=Sunday).
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return False  # fail closed on malformed expressions
    minute, hour, day_of_month, month, day_of_week = parts
    # Convert Python weekday (Mon=0..Sun=6) to cron weekday (Sun=0..Sat=6).
    cron_dow = (now.weekday() + 1) % 7
    return (
        cron_field_matches(minute, now.minute)
        and cron_field_matches(hour, now.hour)
        and cron_field_matches(day_of_month, now.day)
        and cron_field_matches(month, now.month)
        and cron_field_matches_dow(day_of_week, cron_dow)
    )


def cron_field_matches(expression: object, value: int) -> bool:
    text = str(expression).strip()
    if not text or text == "*":
        return True
    if text.startswith("*/"):
        try:
            divisor = int(text[2:])
        except ValueError:
            return False
        return divisor > 0 and value % divisor == 0
    # Range support: e.g. "1-5" matches values in [1, 5].
    if "-" in text and not text.startswith("-"):
        parts = text.split("-")
        if len(parts) == 2:
            try:
                low, high = int(parts[0]), int(parts[1])
            except ValueError:
                return False
            return low <= value <= high
        return False
    # Comma-separated list: e.g. "1,3,5"
    if "," in text:
        for sub in text.split(","):
            sub = sub.strip()
            if not sub:
                continue
            if cron_field_matches(sub, value):
                return True
        return False
    try:
        return value == int(text)
    except ValueError:
        return False


def cron_field_matches_dow(expression: object, value: int) -> bool:
    """Match day-of-week with standard cron semantics (0=Sunday, 7 also = Sunday).

    Supports the same syntax as ``cron_field_matches``: ``*``, ``*/N``, single
    integers, ranges (``1-5``), and comma-separated lists (``1,3,5``).  The value
    7 is normalised to 0 (Sunday) so ``0 9 * * 0`` and ``0 9 * * 7`` match the
    same day.
    """
    text = str(expression).strip()
    if not text or text == "*":
        return True
    if text.startswith("*/"):
        try:
            divisor = int(text[2:])
        except ValueError:
            return False
        return divisor > 0 and value % divisor == 0
    # Range support: e.g. "1-5" matches cron weekdays in [1, 5].
    if "-" in text and not text.startswith("-"):
        parts = text.split("-")
        if len(parts) == 2:
            try:
                low, high = int(parts[0]), int(parts[1])
            except ValueError:
                return False
            # Normalise 7 → 0 so ranges that include 7 behave correctly.
            low = 0 if low == 7 else low
            high = 0 if high == 7 else high
            return low <= value <= high
        return False
    # Comma-separated list: e.g. "1,3,5" or "0,7" (both match Sunday).
    if "," in text:
        for sub in text.split(","):
            sub = sub.strip()
            if not sub:
                continue
            if cron_field_matches_dow(sub, value):
                return True
        return False
    try:
        cron_val = int(text)
    except ValueError:
        return False
    # In standard cron, 7 is equivalent to 0 (Sunday).
    if cron_val == 7:
        cron_val = 0
    return value == cron_val


def validate_cron_string(expr: str) -> str | None:
    parts = expr.strip().split()
    if len(parts) != 5:
        return "Cron expression must have exactly 5 fields: minute hour day_of_month month day_of_week."
    ranges = (
        ("minute", 0, 59),
        ("hour", 0, 23),
        ("day_of_month", 1, 31),
        ("month", 1, 12),
        ("day_of_week", 0, 7),  # 0-6 (Sun-Sat), 7 also = Sunday
    )
    for part, (name, minimum, maximum) in zip(parts, ranges, strict=True):
        error = _validate_cron_field(part, name, minimum, maximum)
        if error:
            return error
    return None


def validate_cron_trigger_config(trigger: str | None, trigger_config: str | None) -> None:
    if trigger != "cron" or not trigger_config:
        return
    try:
        config = json.loads(trigger_config)
    except json.JSONDecodeError as exc:
        raise ValueError("Cron trigger_config must be valid JSON.") from exc
    if not isinstance(config, dict) or "cron" not in config:
        return
    error = validate_cron_string(str(config["cron"]))
    if error:
        raise ValueError(error)


def _validate_cron_field(field: str, name: str, minimum: int, maximum: int) -> str | None:
    if field == "*":
        return None
    if field.startswith("*/"):
        try:
            divisor = int(field[2:])
        except ValueError:
            return f"Invalid cron {name} field: {field!r}."
        if divisor <= 0:
            return f"Invalid cron {name} field: step must be greater than 0."
        return None
    # Range: e.g. "1-5"
    if "-" in field and not field.startswith("-"):
        parts = field.split("-")
        if len(parts) == 2:
            try:
                low, high = int(parts[0]), int(parts[1])
            except ValueError:
                return f"Invalid cron {name} field: {field!r}."
            if not (minimum <= low <= maximum):
                return f"Invalid cron {name} field: range start {low} must be between {minimum} and {maximum}."
            if not (minimum <= high <= maximum):
                return f"Invalid cron {name} field: range end {high} must be between {minimum} and {maximum}."
            if low > high:
                return f"Invalid cron {name} field: range start {low} is greater than end {high}."
            return None
        return f"Invalid cron {name} field: {field!r}."
    # Comma-separated list: e.g. "1,3,5"
    if "," in field:
        for sub in field.split(","):
            sub = sub.strip()
            if not sub:
                return f"Invalid cron {name} field: empty value in list."
            error = _validate_cron_field(sub, name, minimum, maximum)
            if error:
                return error
        return None
    try:
        value = int(field)
    except ValueError:
        return f"Invalid cron {name} field: {field!r}."
    if not minimum <= value <= maximum:
        return f"Invalid cron {name} field: {value} must be between {minimum} and {maximum}."
    return None
