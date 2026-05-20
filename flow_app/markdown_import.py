from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schemas import MarkdownImportItem, TaskStatus

CHECKBOX_PATTERN = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
INLINE_META_PATTERN = re.compile(r"\b(priority|project|assignee|status):\s*([A-Za-z0-9_.@/-]+)", re.IGNORECASE)
LINE_META_PATTERN = re.compile(r"^\s*(priority|project|assignee|status|acceptance|acceptance_criteria|ac):\s*(.+?)\s*$", re.IGNORECASE)
VALID_STATUSES = {"backlog", "todo", "doing", "review", "done"}


@dataclass
class ParsedDraft:
    preview_id: str
    title: str
    status: str
    priority: int
    project: str
    assignee: str | None
    description_lines: list[str] = field(default_factory=list)
    acceptance_lines: list[str] = field(default_factory=list)
    source_filename: str | None = None
    source_line: int | None = None
    source_title: str | None = None


def parse_markdown_tasks(
    markdown: str,
    *,
    source_filename: str | None,
    default_project: str,
    default_status: TaskStatus,
    default_priority: int,
) -> list[MarkdownImportItem]:
    heading_stack: list[tuple[int, str]] = []
    context_lines: list[str] = []
    current: ParsedDraft | None = None
    parsed: list[ParsedDraft] = []

    def flush_current() -> None:
        nonlocal current
        if current is not None:
            _trim_blank_edges(current.description_lines)
            _trim_blank_edges(current.acceptance_lines)
            parsed.append(current)
            current = None

    for index, raw_line in enumerate(markdown.splitlines(), start=1):
        line = raw_line.rstrip()

        heading_match = HEADING_PATTERN.match(line)
        if heading_match:
            flush_current()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_stack = [(existing_level, existing_title) for existing_level, existing_title in heading_stack if existing_level < level]
            heading_stack.append((level, title))
            context_lines = []
            continue

        checkbox_match = CHECKBOX_PATTERN.match(line)
        if checkbox_match:
            flush_current()
            checked = checkbox_match.group(1).lower() == "x"
            raw_title = checkbox_match.group(2).strip()
            clean_title, inline_meta = _extract_inline_metadata(raw_title)
            status = _status_from_meta(inline_meta.get("status")) or ("done" if checked else default_status)
            priority = _priority_from_meta(inline_meta.get("priority"), default_priority)
            project = (inline_meta.get("project") or default_project).strip().lower()
            assignee = _clean_optional(inline_meta.get("assignee"))
            source_title = " / ".join(title for _level, title in heading_stack) or None
            current = ParsedDraft(
                preview_id=f"line-{index}",
                title=clean_title,
                status=status,
                priority=priority,
                project=project,
                assignee=assignee,
                description_lines=list(context_lines),
                source_filename=source_filename,
                source_line=index,
                source_title=source_title,
            )
            context_lines = []
            continue

        meta_match = LINE_META_PATTERN.match(line)
        if current is not None and meta_match:
            key = meta_match.group(1).lower()
            value = meta_match.group(2).strip()
            if key == "priority":
                current.priority = _priority_from_meta(value, current.priority)
            elif key == "project":
                current.project = value.lower()
            elif key == "assignee":
                current.assignee = _clean_optional(value)
            elif key == "status":
                current.status = _status_from_meta(value) or current.status
            else:
                current.acceptance_lines.append(value)
            continue

        if current is not None:
            current.description_lines.append(line.strip())
        elif line.strip():
            context_lines.append(line.strip())

    flush_current()
    return [_to_import_item(item) for item in parsed]


def _extract_inline_metadata(title: str) -> tuple[str, dict[str, str]]:
    metadata: dict[str, str] = {}
    for match in INLINE_META_PATTERN.finditer(title):
        metadata[match.group(1).lower()] = match.group(2).strip()
    clean_title = INLINE_META_PATTERN.sub("", title)
    clean_title = re.sub(r"\s{2,}", " ", clean_title).strip(" -")
    return clean_title or title.strip(), metadata


def _priority_from_meta(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, min(1000, parsed))


def _status_from_meta(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned if cleaned in VALID_STATUSES else None


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _trim_blank_edges(lines: list[str]) -> None:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()


def _to_import_item(item: ParsedDraft) -> MarkdownImportItem:
    description_parts: list[str] = []
    if item.source_title:
        description_parts.append(f"Imported from section: {item.source_title}")
    if item.description_lines:
        description_parts.append("\n".join(item.description_lines).strip())

    return MarkdownImportItem(
        preview_id=item.preview_id,
        title=item.title,
        status=item.status,  # type: ignore[arg-type]
        priority=item.priority,
        project=item.project,
        assignee=item.assignee,
        description="\n\n".join(part for part in description_parts if part),
        acceptance_criteria="\n".join(item.acceptance_lines).strip(),
        source_filename=item.source_filename,
        source_line=item.source_line,
        source_title=item.source_title,
    )
