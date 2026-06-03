# Changelog

All notable changes to Flow are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Dependency graph visualization on board (hover to show parent/child lines)
- Ideas intake overlay with expanded editor
- Settings overlay with API keys and agent management
- Realtime board updates and notification settings
- Discord webhook notification provider
- Dependency-aware task selection for agent dispatch
- Provisioned workspace as subprocess cwd for dispatched agents
- Handoff context in dispatched agent prompts
- Webhook secret rotation CLI
- Public-IP SSRF protection policy
- GitHub social preview image and logo asset

### Fixed
- Return None from decrypt_secret when encryption key is missing
- Hardened error logging and client-safe messages
- SSRF enforcement with is_global allowlist
- Settings flow polish and UI refinements

### Changed
- Extracted board UI route, trimmed route-module imports
- Storage format plan documentation and typed helpers

## [0.1.0] - 2025-05-18

### Added
- Kanban board with five columns (backlog → todo → doing → review → done)
- Role-scoped API keys (admin, architect, implementer, reviewer, read_only)
- MCP interface for LLM agents (JSON-RPC 2.0)
- Ideas intake with promotion to tasks
- Human-required flag with blocker reason
- Qualification fields (complexity, impact, effort, risk)
- Markdown import
- Two built-in themes (Neutral, Axis Love)
- Single-binary deployment (FastAPI + SQLite)
- Docker Compose setup