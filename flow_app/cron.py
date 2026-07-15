from __future__ import annotations

from datetime import datetime
import json


def cron_string_matches(expr: str, now: datetime) -> bool:
    """Match the supported subset of a standard 5-field cron expression."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return True
    minute, hour, day_of_month, month, day_of_week = parts
    return (
        cron_field_matches(minute, now.minute)
        and cron_field_matches(hour, now.hour)
        and cron_field_matches(day_of_month, now.day)
        and cron_field_matches(month, now.month)
        and cron_field_matches(day_of_week, now.weekday())
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
    try:
        return value == int(text)
    except ValueError:
        return False


def validate_cron_string(expr: str) -> str | None:
    parts = expr.strip().split()
    if len(parts) != 5:
        return "Cron expression must have exactly 5 fields: minute hour day_of_month month day_of_week."
    ranges = (
        ("minute", 0, 59),
        ("hour", 0, 23),
        ("day_of_month", 1, 31),
        ("month", 1, 12),
        ("day_of_week", 0, 6),
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
    try:
        value = int(field)
    except ValueError:
        return f"Invalid cron {name} field: {field!r}."
    if not minimum <= value <= maximum:
        return f"Invalid cron {name} field: {value} must be between {minimum} and {maximum}."
    return None
