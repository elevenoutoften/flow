#!/usr/bin/env python3
"""Security scan for Flow repository - checks for secrets, personal info, and unsafe patterns.

Runs as a pre-release gate. Exit 0 if clean, exit 1 if violations found.
Usage: python scripts/scan_secrets.py [--verbose] [--all-revisions]
"""

# FLOW_SCAN_ALLOWLIST: this file defines denylist patterns, not leaked data

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Patterns that indicate leaked secrets
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub PAT", re.compile(r"ghp_[0-9a-zA-Z]{36}")),
    ("GitHub fine-grained PAT", re.compile(r"github_pat_[0-9a-zA-Z_]{82}")),
    ("Generic secret literal", re.compile(r"""(?:secret|token|password)\s*[:=]\s*['"][0-9a-zA-Z_\-]{20,}['"]""", re.IGNORECASE)),
    ("Private key header", re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----")),
    ("URL with credentials", re.compile(r"https?://[^\s:]+:[^\s@]+@[^\s]+")),
    ("IPv4 address literal", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", re.IGNORECASE)),
    ("HTTP endpoint with internal host", re.compile(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})")),
]
SECRET_LABELS = {label for label, _pattern in SECRET_PATTERNS}

# Personal identifiers (real names, personal paths)
PERSONAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Personal name: anton", re.compile(r"\banton\b", re.IGNORECASE)),
    ("Personal name: wife", re.compile(r"\bwife\b", re.IGNORECASE)),
    ("Personal path: /home/nyx", re.compile(r"/home/nyx")),
    ("Personal path: /home/dev", re.compile(r"/home/dev")),
    ("Legacy project name: gpu-box", re.compile(r"\bgpu-box\b", re.IGNORECASE)),
    ("Legacy project name: GPUBox", re.compile(r"\bGPUBox\b")),
    ("Legacy project name: gpubox", re.compile(r"\bgpubox\b", re.IGNORECASE)),
]

# Allowlisted matches (false positives)
# These are known-safe occurrences that should not trigger alerts.
ALLOWLIST: dict[str, list[tuple[str, int]]] = {
    # file_pattern: [(matched_text, line_number_or_0_for_any), ...]
}

# Strings that are explicitly allowlisted per-file - these are test fixtures
# or documentation that intentionally use generic placeholders.
ALLOWLISTED_CONTEXTS: list[str] = [
    # "axis-love" is a product theme identifier, not a personal reference
    "axis-love",
    # "codex" is an OpenAI product name used as a test fixture
    "codex",
    # "admin_user" and "viewer" are the approved generic replacements for personal names
    "admin_user",
    "viewer",
    # "127.0.0.1" and "localhost" in docs and configs are intentional local references
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    # "example.com" is the RFC 2606 safe domain for examples
    "example.com",
]

ALLOWLISTED_FILES: list[str] = [
    # The scanner script itself defines the denylist patterns - those strings are
    # pattern definitions, not actual leaked personal identifiers.
    "scripts/scan_secrets.py",
]

def is_allowlisted(label: str, filepath: str, line: str) -> bool:
    """Check if a match is a known false positive."""
    # Check file-based allowlist
    normalized_filepath = filepath.replace(os.sep, "/")
    for allowed_file in ALLOWLISTED_FILES:
        if allowed_file in normalized_filepath or normalized_filepath.endswith(allowed_file):
            return True
    # Check context-based allowlist
    for ctx in ALLOWLISTED_CONTEXTS:
        if ctx.lower() in line.lower():
            return True
    return False


def git_tracked_files() -> list[Path]:
    """Get all git-tracked files, excluding binary and generated files."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        p = REPO_ROOT / line
        if p.suffix in {".pyc", ".pyo", ".so", ".png", ".jpg", ".gif", ".ico", ".woff", ".woff2"}:
            continue
        if ".min." in p.name:
            continue
        if p.exists():
            files.append(p)
    return files


def scan_file(filepath: Path, patterns: list[tuple[str, re.Pattern[str]]], verbose: bool) -> list[tuple[str, str, int, str]]:
    """Scan a file for pattern matches. Returns list of (label, filepath, lineno, line)."""
    violations = []
    try:
        content = filepath.read_text(errors="replace")
    except Exception:
        return []

    for lineno, line in enumerate(content.splitlines(), 1):
        for label, pattern in patterns:
            if pattern.search(line):
                if not is_allowlisted(label, str(filepath), line):
                    violations.append((label, str(filepath.relative_to(REPO_ROOT)), lineno, line.strip()))
    return violations


def scan_all_revisions(patterns: list[tuple[str, re.Pattern[str]]], verbose: bool) -> list[tuple[str, str, str, str]]:
    """Scan all git revisions for secret patterns. Returns (label, revision, file, line)."""
    # Get all revision hashes
    result = subprocess.run(
        ["git", "rev-list", "--all"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    violations = []
    for rev in result.stdout.strip().split("\n"):
        if not rev:
            continue
        for label, pattern in patterns:
            r = subprocess.run(
                ["git", "grep", "-n", pattern.pattern, rev],
                capture_output=True, text=True, cwd=REPO_ROOT,
            )
            for line in r.stdout.strip().split("\n"):
                # Skip matches in allowlisted files (e.g., the scanner script itself
                # which contains the denylist pattern definitions)
                skip = False
                for allowed_file in ALLOWLISTED_FILES:
                    if allowed_file in line or line.endswith(allowed_file):
                        skip = True
                        break
                parts = line.split(":", 2)
                grep_filepath = parts[1] if len(parts) >= 2 else ""
                if line and not skip and not is_allowlisted(label, grep_filepath, line):
                    violations.append((label, rev[:8], line[:120]))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Flow repo for secrets and personal info")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all matches including allowlisted")
    parser.add_argument("--all-revisions", action="store_true", help="Scan git history (all refs)")
    args = parser.parse_args()

    all_violations = []

    # Scan tracked files
    files = git_tracked_files()
    print(f"Scanning {len(files)} tracked files...")

    for category, patterns in [("Secrets", SECRET_PATTERNS), ("Personal info", PERSONAL_PATTERNS)]:
        category_violations = []
        for filepath in files:
            violations = scan_file(filepath, patterns, args.verbose)
            category_violations.extend(violations)

        if category_violations:
            print(f"\n{category} violations:")
            for label, relpath, lineno, line in category_violations:
                print(f"  [{label}] {relpath}:{lineno}")
                if args.verbose:
                    # Truncate long lines but don't echo full secret values
                    display = line[:100] + "..." if len(line) > 100 else line
                    print(f"    {display}")
            all_violations.extend(category_violations)
        else:
            print(f"  {category}: clean [OK]")

    # Scan git history if requested
    if args.all_revisions:
        print(f"\nScanning git history (all refs)...")
        history_violations = scan_all_revisions(SECRET_PATTERNS + PERSONAL_PATTERNS, args.verbose)
        if history_violations:
            print(f"  Found {len(history_violations)} matches in history")
            for label, rev, line in history_violations[:20]:
                print(f"  [{label}] {rev}: {line[:80]}")
            all_violations.extend(history_violations)
        else:
            print(f"  History: clean [OK]")

    # Also check .gitignore coverage
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    required_ignores = ["data/", "*.sqlite", ".env"]
    for pattern in required_ignores:
        if pattern not in gitignore:
            print(f"  WARNING: .gitignore missing pattern: {pattern}")
            all_violations.append(("gitignore", f".gitignore:{pattern}", 0, f"Missing {pattern}"))

    if all_violations:
        print(f"\n[FAIL] {len(all_violations)} violation(s) found. Fix before release.")
        return 1

    print(f"\n[PASS] All checks passed. Repository is clean for release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
