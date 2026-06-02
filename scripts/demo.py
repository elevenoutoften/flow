#!/usr/bin/env python
"""Local demo launcher for the Flow web UI.

Seeds a throwaway SQLite database with synthetic projects, tasks, dependency
links, and ideas that exercise every board feature (all five columns, priority
levels, assignees, unclaimed tasks, human-required blockers, dependencies,
triage fields, notes, and the idea wall), then starts the server and opens it
in your browser.

For frictionless local clicking, every browser request is tagged as the admin
session, so create/claim/move/done/notes and API-key management all work without
a header-injecting proxy or extension. This is a DEV-ONLY convenience that lives
here in the launcher, never in the app itself.

Usage:
    python scripts/demo.py            # fresh data, open browser on :8100
    python scripts/demo.py --port 9000
    python scripts/demo.py --keep     # reuse the existing demo DB

The demo database (.flow-demo.db) is git-ignored and never touches real data.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # allow `python scripts/demo.py` from anywhere

DEMO_DB = REPO_ROOT / ".flow-demo.db"
DEMO_DB_URL = "sqlite:///" + DEMO_DB.as_posix()


# --------------------------------------------------------------------------- #
# Synthetic data — Flow & Lore feature showcase (safe for screenshots).
# --------------------------------------------------------------------------- #

PROJECTS = [
    {"slug": "flow", "name": "Flow", "description": "Agent-first Kanban task board.",
     "repo_url": "https://github.com/axis/flow.git", "default_branch": "main"},
    {"slug": "lore", "name": "Lore", "description": "Canonical knowledge base for agents and humans.",
     "repo_url": "https://github.com/axis/lore.git", "default_branch": "main"},
    {"slug": "axis-web", "name": "Axis Web", "description": "Marketing site and product landing pages.",
     "repo_url": "https://github.com/axis/web.git", "default_branch": "main"},
    {"slug": "hermes", "name": "Hermes", "description": "Autonomous agent runner and dispatcher.",
     "repo_url": "https://github.com/axis/hermes.git", "default_branch": "main"},
    {"slug": "docs", "name": "Docs", "description": "Product documentation and guides.",
     "repo_url": "https://github.com/axis/docs.git", "default_branch": "main"},
]

# key -> TaskCreate kwargs. Priorities span 0-1000 so every priority-dot level
# (>=900: 3, >=700: 2, >=200: 1, else 0) is represented.
TASKS = {
    # ---- backlog ----
    "recurring": {"project": "flow", "status": "backlog", "priority": 300,
                  "title": "Recurring task templates",
                  "description": "Materialize repeating work from a schedule so standups, releases, and audits show up automatically.",
                  "complexity": "medium", "impact": "medium", "effort": "medium", "risk": "low"},
    "saved-views": {"project": "flow", "status": "backlog", "priority": 250,
                    "title": "Saved board filters & views",
                    "description": "Let operators pin a project + filter combination and jump back to it in one click."},
    "backlinks": {"project": "lore", "status": "backlog", "priority": 180,
                  "title": "Wiki-style backlinks between pages",
                  "description": "Surface every page that references the current one, so canon stays connected."},
    "autoscale": {"project": "hermes", "status": "backlog", "priority": 420,
                  "title": "Auto-scale the runner pool on queue depth",
                  "description": "Spin runners up and down based on pending dispatch load.",
                  "complexity": "large", "impact": "high", "effort": "high", "risk": "high"},
    "pricing": {"project": "axis-web", "status": "backlog", "priority": 150,
                "title": "Pricing page with plan comparison",
                "description": "Side-by-side tiers with a feature matrix and a clear call to action."},

    # ---- todo ----
    "md-import": {"project": "flow", "status": "todo", "priority": 650, "assignee": "scout",
                  "title": "Markdown task import wizard",
                  "description": "Paste a checklist and preview the parsed tasks before committing them to the board.",
                  "acceptance_criteria": "- Preview shows status, project, priority per line\n- Duplicates are flagged before commit\n- Commit reports created vs skipped"},
    "notify": {"project": "flow", "status": "todo", "priority": 700,
               "title": "Discord & Telegram notifications",
               "description": "Route human-required blockers and task events to chat so nothing waits unseen."},
    "lore-autorecord": {"project": "lore", "status": "todo", "priority": 600, "assignee": "atlas",
                        "title": "Auto-record outcomes after Flow tasks",
                        "description": "When a Flow task closes, write what became true to the matching Lore page."},
    "landing": {"project": "axis-web", "status": "todo", "priority": 820,
                "title": "Landing page: “Flow — the agent-first task board”",
                "description": "Hero, feature grid, and a live board screenshot. The flagship marketing page.",
                "impact": "high", "complexity": "medium"},
    "quickstart": {"project": "docs", "status": "todo", "priority": 500,
                   "title": "Quickstart: run Flow locally in 2 minutes",
                   "description": "A copy-paste path from clone to a populated board in the browser."},

    # ---- doing ----
    "rules": {"project": "flow", "status": "doing", "priority": 900, "assignee": "hermes",
              "title": "Automation rules engine",
              "description": "Event-driven conditions and actions: auto-assign, notify, promote, and escalate.",
              "complexity": "large", "impact": "high", "effort": "high", "risk": "medium",
              "acceptance_criteria": "- Conditions evaluate task fields and events\n- Actions can notify and mutate tasks\n- Rules are listed and toggleable in Settings"},
    "human-blockers": {"project": "flow", "status": "doing", "priority": 850, "assignee": "nyx",
                       "title": "Human-required blockers with reasons",
                       "human_required": True,
                       "blocker_reason": "Needs product sign-off on the blocker copy before GA.",
                       "description": "Flag tasks that an agent must not complete alone, with a visible reason.",
                       "impact": "high", "risk": "medium"},
    "lore-search": {"project": "lore", "status": "doing", "priority": 780, "assignee": "atlas",
                    "title": "Full-text search across the knowledge base",
                    "description": "Fast lookup over titles and bodies so agents find canon before re-deriving it.",
                    "complexity": "medium", "impact": "high"},
    "workspace-iso": {"project": "hermes", "status": "doing", "priority": 760, "assignee": "hermes",
                      "title": "Workspace isolation for parallel agent runs",
                      "description": "Each dispatched run gets its own git worktree so concurrent agents never collide.",
                      "complexity": "large", "risk": "high", "impact": "high"},
    "screenshots": {"project": "docs", "status": "doing", "priority": 640, "assignee": "scout",
                    "title": "Demo screenshots for the new flow2 UI",
                    "description": "Capture board, detail drawer, ideas wall, and each accent theme for the docs and landing page."},

    # ---- review ----
    "themes": {"project": "flow", "status": "review", "priority": 720, "assignee": "nyx",
               "title": "Live theme switcher — Axis Love / Teal / Leaf",
               "description": "Swap the accent color live across the board; choice persists per browser.",
               "acceptance_criteria": "- Axis Love is the default rose accent\n- Teal and Leaf swap the accent only\n- Choice is saved and re-applied on reload"},
    "drag": {"project": "flow", "status": "review", "priority": 680, "assignee": "hermes",
             "title": "Drag-and-drop with safe click handling",
             "description": "Move cards between columns by pointer drag without accidentally opening the detail drawer."},
    "lore-mcp": {"project": "lore", "status": "review", "priority": 700, "assignee": "atlas",
                 "title": "MCP tools for page upsert & lookup",
                 "description": "Expose Lore over MCP so agents read and write canon programmatically."},
    "api-keys": {"project": "flow", "status": "review", "priority": 900, "assignee": "nyx",
                 "title": "API key roles & one-time secrets",
                 "human_required": True,
                 "blocker_reason": "Security review required before this can merge.",
                 "description": "Scoped roles (read-only → admin) and a generated secret shown exactly once.",
                 "impact": "critical", "risk": "high", "complexity": "medium"},

    # ---- done ----
    "api": {"project": "flow", "status": "done", "priority": 950, "assignee": "hermes",
            "title": "Agent-first REST + MCP API",
            "description": "A complete HTTP and MCP surface so agents have a reliable source of truth for work."},
    "deps": {"project": "flow", "status": "done", "priority": 880, "assignee": "hermes",
             "title": "Task dependencies & auto-promotion",
             "description": "Link blocking tasks; children promote automatically when their blockers finish."},
    "ideas": {"project": "flow", "status": "done", "priority": 600, "assignee": "scout",
              "title": "Idea wall for lightweight capture",
              "description": "Jot a title, description, and project without leaving the board."},
    "lore-pages": {"project": "lore", "status": "done", "priority": 900, "assignee": "atlas",
                   "title": "Canonical pages with frontmatter & kinds",
                   "description": "Typed knowledge pages with visibility and metadata, ready for humans and agents."},
    "lore-acl": {"project": "lore", "status": "done", "priority": 820, "assignee": "atlas",
                 "title": "Page visibility & access control",
                 "description": "Public, internal, and private pages enforced consistently across the API and MCP."},
    "dispatch": {"project": "hermes", "status": "done", "priority": 860, "assignee": "hermes",
                 "title": "Dependency-aware task dispatch",
                 "description": "The dispatcher only hands an agent work whose blockers are already resolved."},
}

# (parent, child, link_type) — parent blocks/precedes child.
LINKS = [
    ("screenshots", "landing", "blocks"),       # landing page waits on demo screenshots
    ("api", "rules", "blocks"),                  # rules engine builds on the API
    ("api", "lore-mcp", "blocks"),               # MCP tools build on the API
    ("lore-search", "lore-autorecord", "blocks"),  # auto-record needs search
    ("quickstart", "landing", "related"),        # related marketing/docs work
]

# task key -> list of (author, note body)
NOTES = {
    "rules": [
        ("hermes", "Condition evaluation is wired; working on the action executors next."),
        ("nyx", "Let's make sure notify actions route through the providers from #notify."),
    ],
    "lore-search": [
        ("atlas", "Indexing titles + bodies. Ranking pass still pending."),
    ],
    "api-keys": [
        ("nyx", "One-time secret reveal is done; awaiting the security review before merge."),
    ],
}

IDEAS = [
    {"project": "flow", "author": "nyx", "title": "Burndown & velocity charts",
     "description": "A small analytics surface: throughput per column and time-in-status."},
    {"project": "flow", "author": "scout", "title": "Board swimlanes by assignee",
     "description": "Group cards into horizontal lanes per agent to see who is loaded."},
    {"project": "flow", "author": "hermes", "title": "Slack notification provider",
     "description": "Add Slack alongside Discord and Telegram for blocker alerts."},
    {"project": "lore", "author": "atlas", "title": "Semantic search with embeddings",
     "description": "Complement full-text search with vector similarity for fuzzy recall."},
    {"project": "lore", "author": "atlas", "title": "Auto-summary of stale pages",
     "description": "Flag pages untouched for 90 days and propose a refreshed summary."},
    {"project": "axis-web", "author": "scout", "title": "Interactive demo embed",
     "description": "Drop a read-only live board onto the landing page so visitors can poke around."},
]


def seed() -> None:
    """Populate a fresh demo database with the synthetic dataset above."""
    from flow_app.database import Base, build_engine, build_session_factory
    from flow_app.migration import ensure_compatible_schema
    from flow_app import repository as repo
    from flow_app.schemas import IdeaCreate, ProjectCreate, TaskCreate, TaskLinkCreate

    engine = build_engine(DEMO_DB_URL)
    Base.metadata.create_all(bind=engine)
    ensure_compatible_schema(engine)
    session = build_session_factory(engine)()
    try:
        for project in PROJECTS:
            repo.create_project(session, ProjectCreate(**project))
        session.flush()

        created = {}
        for key, payload in TASKS.items():
            created[key] = repo.create_task(session, TaskCreate(**payload))
        session.flush()

        for parent, child, link_type in LINKS:
            repo.create_task_link(
                session,
                TaskLinkCreate(parent_id=created[parent].id, child_id=created[child].id, link_type=link_type),
            )

        for key, notes in NOTES.items():
            for author, body in notes:
                repo.add_note(session, created[key], body, author=author)

        for idea in IDEAS:
            repo.create_idea(session, IdeaCreate(**idea))

        session.commit()
    finally:
        session.close()
        engine.dispose()

    print(
        f"  Seeded {len(PROJECTS)} projects, {len(TASKS)} tasks, "
        f"{len(LINKS)} dependency links, {len(IDEAS)} ideas."
    )


class _AdminBrowserMiddleware:
    """DEV-ONLY: tag every HTTP request as the trusted admin session so all UI
    actions work locally without a header-injecting proxy or browser extension."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = [(k, v) for (k, v) in scope.get("headers", []) if k != b"x-axis-admin"]
            headers.append((b"x-axis-admin", b"1"))
            scope = dict(scope, headers=headers)

        async def send_no_cache(message):
            if message.get("type") == "http.response.start":
                headers = [(k, v) for (k, v) in message.get("headers", []) if k.lower() not in {b"cache-control", b"pragma", b"expires"}]
                headers.extend(
                    [
                        (b"cache-control", b"no-store, max-age=0"),
                        (b"pragma", b"no-cache"),
                        (b"expires", b"0"),
                    ]
                )
                message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, send_no_cache)


def _pick_port(preferred: int, attempts: int = 20) -> int:
    """Return the first bindable localhost port at or above ``preferred``.

    Avoids the WinError 10048 "address already in use" crash when a stale server
    (or another app) is squatting on the requested port.
    """
    for candidate in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    return preferred


def _open_browser_when_ready(url: str) -> None:
    import time
    import urllib.request

    for _ in range(60):
        try:
            urllib.request.urlopen(url + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    webbrowser.open(url + f"/?demo={time.time_ns()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic data and launch the Flow web UI.")
    parser.add_argument("--port", type=int, default=8100, help="Port to serve on (default: 8100).")
    parser.add_argument("--keep", action="store_true", help="Reuse the existing demo DB instead of reseeding.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically.")
    args = parser.parse_args()

    # Configure the environment before the app is imported/initialised so
    # get_settings() picks these up. Browser requests are auto-authenticated as
    # admin (see the middleware below).
    os.environ["FLOW_DATABASE_URL"] = DEMO_DB_URL
    os.environ.setdefault("FLOW_DEFAULT_PROJECT", "flow")
    os.environ.setdefault("FLOW_SESSION_SECRET", "demo-secret-key")
    os.environ.setdefault("FLOW_TRUSTED_HEADERS", "1")
    os.environ.setdefault("FLOW_THEME", "love")

    fresh = not args.keep or not DEMO_DB.exists()
    if fresh:
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(DEMO_DB) + suffix)
            if path.exists():
                path.unlink()
        print("Seeding fresh demo database...")
        seed()
    else:
        print("Reusing existing demo database (--keep).")

    import uvicorn
    from flow_app.main import create_app

    app = create_app(
        database_url=DEMO_DB_URL,
        trusted_headers=True,
        session_secret=os.environ["FLOW_SESSION_SECRET"],
    )
    application = _AdminBrowserMiddleware(app)

    port = _pick_port(args.port)
    if port != args.port:
        print(f"  Port {args.port} is busy; using {port} instead.")
    url = f"http://localhost:{port}"
    print(f"\n  Flow demo running at {url}")
    print("  Browser requests are auto-authenticated as admin (local dev only).")
    print("  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
