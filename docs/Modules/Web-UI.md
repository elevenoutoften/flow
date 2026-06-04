# Web UI

**Scope:** `flow_app/routes/ui.py`, `flow_app/templates/`, `flow_app/static/`

The browser-facing surface ("flow2") is a server-rendered Jinja2 board plus two
secondary surfaces loaded as overlays. It is a thin presentation layer over the
REST API — every mutation it performs goes through the same `/api/*` endpoints
documented in [REST API](REST-API.md), so the API remains the source of truth.

## Surfaces

| Route | Template | Static assets | Purpose |
|-------|----------|---------------|---------|
| `GET /` | `board.html` | `flow.css`, `flow.js` | Kanban board: columns, cards, task detail, create-task, drag-to-move |
| `GET /ideas.html` | `ideas.html` | `flow-ideas.css`, `flow-ideas.js` | Idea wall with inline create/edit/archive |
| `GET /settings.html` | `settings.html` | `flow-settings.css`, `flow-settings.js` | Projects, appearance, API keys, markdown import; placeholders for not-yet-built sections |

The board is the host page. **Ideas** and **Settings** are opened as `<iframe>`
overlays inside the board (`openIdeas()` / `openSettings()` in `flow.js`). They
load the same routes with `?embedded=1`, which switches the surface into
chrome-less embedded styling.

### Cross-frame messaging

Embedded surfaces talk to the host via `window.postMessage`:

| Message | Sent by | Host reaction |
|---------|---------|---------------|
| `flow:close-ideas` / `flow:close-settings` | child close button / Escape | Close the overlay |
| `flow:ideas-mutated` / `flow:settings-mutated` | child after a successful API write | Mark the board dirty; reload **once the overlay is dismissed** |
| `flow:theme` | settings appearance control | Apply the accent theme to the board live |

The board never reloads while an overlay is open. A mutation only sets a
`boardDirty` flag; the actual reload is deferred to `closeIdeas()` /
`closeSettings()`. This keeps in-flight content alive — most importantly the
**one-time API key**, which is shown only once and would otherwise be destroyed
by an immediate parent reload.

## Asset versioning

Templates reference static files as `/static/<file>?v={{ asset_version }}`.
`asset_version` is `FLOW_VERSION`, a content hash of the static directory
(see [Architecture](../Architecture.md)). Changing any CSS/JS changes the hash
and busts the browser cache automatically — there is no manual version bump.

## Theme

flow2 is the source of truth for the theme design. A theme swaps the **accent
(rose) token family only** — surfaces, type, and layout stay identical. The
accent themes exposed in **Settings → Appearance** are, in order:

| Token | Label | Accent |
|-------|-------|--------|
| `love` | Axis Love (default) | Rose / pink (`#ff8fbe`, the `:root` palette) |
| `teal` | Axis Teal | Cyan / tiffany (`#4fd6cf`) |
| `leaf` | Axis Leaf | Green / chartreuse (`#a3e635`) |
| `neutral` | Neutral | Grey (`#9ca3af`) |

The server-side default is `FLOW_THEME` (defaults to `love`). The board renders
`<html data-theme="…">` and the settings surface `<body data-theme="…">` from
that default. A per-browser choice in Settings is persisted to
`localStorage["flow.theme"]` and re-applied on load, overriding the server
default. Selecting a theme in the embedded Settings surface also posts a
`flow:theme` message so the board updates live without a reload. Theme is a
purely client-side accent swap; it does not change server state.

Each accent is defined once per surface — `[data-theme="…"]` in `flow.css`
(board) and `body[data-theme="…"]` in `flow-settings.css` (settings). `love` is
the shared `:root` default, so it needs no override block.

## Capability gaps: engine vs. UI

The backend supports more than the current flow2 design surfaces. These are
**intentional UI gaps, not missing engine features** — use the REST API or MCP
for them until the UI grows the controls:

- **Task field editing.** The API supports `PATCH /api/tasks/{id}` for `title`,
  `description`, `priority`, `status`, `assignee`, the qualification fields
  (`complexity`/`impact`/`effort`/`risk`), `human_required`, and
  `blocker_reason`. The flow2 detail drawer is **read + action only**: it
  exposes claim / release / done, note composing, and drag-to-move (which calls
  `/move`), but does not render an edit form for the other fields. The hidden
  `detail-form` in `board.html` is hydrated but intentionally not wired to a
  submit in the current design. To edit those fields today, call the API/MCP.
- **Acceptance-criteria checkboxes** in the detail drawer are a local
  convenience only — their checked state is stored in `localStorage` per browser
  and is **not** persisted to the server.
- **Settings sections 04-15** keep the flow2 sidebar shape but are still
  disabled placeholders in `settings.html`. Agents, Workspaces, Agent Runs,
  Automation Rules, and Webhooks already have REST APIs available, so their next
  step is UI wiring. Schedules, Notifications settings, Secrets, Adapters,
  Remote Runners, Ops / Audit, and Backup / Restore still need backend routes
  before their reference controls can be made live.
- **Embedded-surface auth** relies on the signed session cookie. Set
  `FLOW_SESSION_SECRET` so the board issues a session cookie; header-only admin
  auth (`X-Axis-Admin`) is not forwarded into the iframes, so without a session
  secret the Settings surface will see an unauthenticated actor and disable API
  key management. See [Security](Security.md).
