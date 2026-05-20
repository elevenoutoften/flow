# Release Checklist

Before packaging, publishing, or sharing the repository:

## 1. Clean Working Tree

```bash
git status --short
```

Ensure no uncommitted changes. No generated databases, secrets, or agent artifacts should appear.

## 2. Verify .gitignore Coverage

```bash
git ls-files --others --exclude-standard
```

All untracked files should be ignored. If any generated files appear, add them to `.gitignore`.

Items explicitly ignored:
- `data/` — runtime SQLite databases
- `*.sqlite`, `*.sqlite3`, `*.db` — any database files
- `.env`, `.env.*` — secrets and environment configuration
- `.codex-task-*.md` — agent task spec files
- `.worktrees/` — workspace git worktrees

## 3. Purge Generated Data

```bash
# Remove runtime database
rm -f data/flow.sqlite
rm -rf .pytest-tmp/
rm -f data/*.sqlite

# Remove any stray test databases in project root
rm -f *.sqlite *.db

# Remove agent workspace artifacts
rm -f .codex-task-*.md
rm -rf .worktrees/
```

## 4. Run Security Scan

```bash
python scripts/scan_secrets.py --verbose
```

Must exit 0 (clean). For a full history scan:

```bash
python scripts/scan_secrets.py --verbose --all-revisions
```

Manual spot-checks if not using the script:

```bash
# Check for accidentally committed secrets
grep -rn "api_key\|secret\|password\|token" --include="*.py" --include="*.md" --include="*.yaml" | grep -v "test" | grep -v "def " | grep -v "class " | grep -v "# "

# Check for personal identifiers (update pattern for your project)
grep -rn "<personal-name>\|<personal-path>" --include="*.py" --include="*.md" --include="*.html" --include="*.js"
```

All scans should return empty results.

## 5. Run Tests

```bash
python -m pytest tests/ -v
```

All tests must pass.

## 6. Version Bump

Update `FLOW_VERSION` in `flow_app/config.py` if this is a release.

## 7. Push and Tag

```bash
git push origin main
git tag v0.X.Y
git push origin v0.X.Y
```

## 8. Commit Author Policy

This repository uses an orphan commit history. All commits should use:

- **Author:** `Nyx Prime <nyx@axis.love>` (project bot)
- **Committer:** Same as author unless contributed by a human contributor

For human contributions, set committer to the human's identity:

```bash
git commit --author="Nyx Prime <nyx@axis.love>"
```

Do not use personal email addresses in commit metadata for this public repository.
