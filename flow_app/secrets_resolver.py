from __future__ import annotations

import os
from pathlib import Path


class SecretResolutionError(ValueError):
    """Raised when a secret reference cannot be resolved safely."""


def is_secret_reference(value: str) -> bool:
    return value.startswith(("env:", "file:"))


def resolve_secret(value: str) -> str:
    if not is_secret_reference(value):
        return value
    if value.startswith("env:"):
        return _resolve_env_secret(value)
    if value.startswith("file:"):
        return _resolve_file_secret(value)
    return value


def resolve_secrets(values: dict[str, str]) -> dict[str, str]:
    return {key: resolve_secret(value) for key, value in values.items()}


def redact_secret(value: str) -> str:
    if is_secret_reference(value):
        return value
    return "***" if value else ""


def secret_reference_type(value: str | None) -> str:
    if not value:
        return "none"
    if value.startswith("env:"):
        return "env"
    if value.startswith("file:"):
        return "file"
    return "literal"


def _resolve_env_secret(reference: str) -> str:
    name = reference.removeprefix("env:").strip()
    if not name:
        raise SecretResolutionError("Invalid secret reference 'env:': missing environment variable name.")
    value = os.environ.get(name, "")
    if not value:
        raise SecretResolutionError(f"Unable to resolve secret reference '{reference}': environment variable is missing or empty.")
    return value


def _resolve_file_secret(reference: str) -> str:
    raw_path = reference.removeprefix("file:").strip()
    if not raw_path:
        raise SecretResolutionError("Invalid secret reference 'file:': missing file path.")
    path = Path(raw_path).expanduser()
    try:
        resolved_path = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SecretResolutionError(f"Unable to resolve secret reference '{reference}': file was not found.") from exc
    except OSError as exc:
        raise SecretResolutionError(f"Unable to resolve secret reference '{reference}': file path could not be resolved.") from exc

    allowed_roots = _allowed_file_roots()
    if not any(_is_relative_to(resolved_path, root) for root in allowed_roots):
        roots = ", ".join(str(root) for root in allowed_roots)
        raise SecretResolutionError(
            f"Unable to resolve secret reference '{reference}': file path is outside allowed roots ({roots})."
        )

    try:
        value = resolved_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SecretResolutionError(f"Unable to resolve secret reference '{reference}': file could not be read.") from exc
    if not value:
        raise SecretResolutionError(f"Unable to resolve secret reference '{reference}': file is empty.")
    return value


def _allowed_file_roots() -> list[Path]:
    roots = [Path("/etc/flow"), Path("/opt/flow")]
    data_dir = os.environ.get("FLOW_DATA_DIR", "./data").strip() or "./data"
    roots.append(Path(data_dir).expanduser())
    extra_roots = os.environ.get("FLOW_SECRET_FILE_ROOTS", "")
    for raw_root in extra_roots.replace(",", os.pathsep).split(os.pathsep):
        cleaned = raw_root.strip()
        if cleaned:
            roots.append(Path(cleaned).expanduser())
    resolved_roots: list[Path] = []
    for root in roots:
        try:
            resolved_roots.append(root.resolve(strict=False))
        except OSError:
            continue
    return resolved_roots


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
