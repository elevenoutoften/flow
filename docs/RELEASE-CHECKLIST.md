# Flow Public Release Checklist

## Pre-Release

- [ ] All tests pass: `uv run --extra test python -m pytest tests/ -v`
- [ ] No hardcoded secrets or API keys in source or docs (grep for `flow_`, `sk-`, `ghp_`, passwords)
- [ ] No internal-specific URLs as defaults (grep for internal hostnames outside of comments/auth headers)
- [ ] Default project is "default" not any internal project name
- [ ] LICENSE file added (MIT)
- [ ] pyproject.toml license field matches LICENSE file
- [ ] README.md reflects the chosen license
- [ ] All Flow board tasks marked done
- [ ] Branch is clean: `git status` shows no uncommitted changes
- [ ] Branch is pushed: `git log --oneline origin/main` matches local

## Code Quality

- [ ] `flow_app/config.py` reads all env vars with defaults
- [ ] `/healthz` returns 503 on database failure
- [ ] Role permissions enforced on all write endpoints (test suite covers this)
- [ ] MCP write tools enforce same permissions as REST
- [ ] No import from outside `flow_app/` (self-contained package)

## Documentation

- [ ] README.md: quickstart, Docker, architecture, features, doc links
- [ ] AGENTS.md + docs/AGENT-QUICKSTART.md: one-command setup, connect-an-agent, agent loop
- [ ] docs/Modules/REST-API.md: all REST endpoints documented with examples
- [ ] docs/Modules/MCP.md: all MCP tools documented with schemas and examples
- [ ] docs/Modules/Security.md: role matrix, human-required permissions, key management
- [ ] docs/Operations.md: backup/restore, schema migration, verification
- [ ] docs/UPGRADE.md: live upgrade runbook, key rotation, rollback

## Standalone Verification

- [ ] `uv run --extra test python -m pytest tests/ -v` passes from repo root
- [ ] Docker Compose works: `docker compose up -d && curl http://localhost:8100/healthz`
- [ ] No references to internal hostnames or private repos in default config

## Final Sign-Off

- [ ] Human review: all acceptance criteria met
- [ ] Human review: no internal-only assumptions remain
- [ ] Human sign-off: ready for public repo publication — _(name, date)_

---

This checklist can be completed without reading the planning conversation. Each item references exact commands or file paths.