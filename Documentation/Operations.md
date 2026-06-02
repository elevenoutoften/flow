# Operations

## Installation

### From Source

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[test]"
```

### From Package

```bash
pip install .
```

### Docker

```bash
docker compose up -d
```

The web service is available at `http://localhost:8100`. The runner is opt-in:

```bash
docker compose --profile runner up -d
```

## Configuration

Flow reads all configuration from environment variables. No config files.

| Variable | Default | Description |
|----------|---------|-------------|
| `FLOW_DATA_DIR` | `./data` | Directory for SQLite database |
| `FLOW_DATABASE_URL` | `sqlite:///{data_dir}/flow.sqlite` | Full database URL (overrides `FLOW_DATA_DIR`) |
| `FLOW_DEFAULT_PROJECT` | `default` | Default project slug |
| `FLOW_HOST` | `0.0.0.0` | Bind address |
| `FLOW_PORT` | `8100` | Bind port |
| `FLOW_DEBUG` | `false` | Debug mode |
| `FLOW_THEME` | `neutral` | UI theme (`neutral` or `axis_love`) |
| `FLOW_TRUSTED_HEADERS` | `false` | Trust `X-Axis-*` proxy headers |
| `FLOW_SESSION_SECRET` | *(empty)* | Secret for browser session cookies |
| `FLOW_SESSION_COOKIE_SECURE` | `false` | Mark session cookies as Secure (HTTPS) |
| `FLOW_CORS_ORIGINS` | *(empty)* | Comma-separated CORS allowed origins |
| `FLOW_SQLITE_JOURNAL_MODE` | `WAL` | SQLite journal mode |
| `FLOW_SQLITE_BUSY_TIMEOUT_MS` | `5000` | SQLite busy timeout in milliseconds |
| `FLOW_TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram bot token for notifications |
| `FLOW_TELEGRAM_CHAT_ID` | *(empty)* | Telegram chat ID for notifications |
| `FLOW_DISCORD_WEBHOOK_URL` | *(empty)* | Discord webhook URL for notifications |
| `FLOW_WEBHOOK_ENCRYPTION_KEY` | *(empty)* | Fernet key for encrypting webhook secrets at rest |
| `FLOW_MAX_WEBHOOK_PAYLOAD_BYTES` | `65536` | Max stored webhook payload size |
| `FLOW_MAX_WEBHOOK_RESPONSE_BYTES` | `4096` | Max stored webhook response body size |
| `FLOW_MAX_WEBHOOK_DELIVERY_AGE_DAYS` | `30` | Retention window for webhook deliveries |

### Runner Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FLOW_RUNNER_PROFILES` | *(required)* | Comma-separated agent profile names |
| `FLOW_RUNNER_INTERVAL` | `30` | Seconds between runner passes |
| `FLOW_BASE_URL` | `http://127.0.0.1:8100` | Flow API base URL |
| `FLOW_API_KEY` | *(empty)* | API key for subprocess dispatch |
| `FLOW_RUNNER_DRY_RUN` | `false` | Log planned work without executing |

## First-Time Setup

### Bootstrap

```bash
flow-bootstrap
```

Creates the default project, three API keys (admin, implementer, reviewer), two agents (smoke-test, reviewer-agent), a workspace config, and four automation rules. Prints API keys — save them immediately, they are shown only once.

Options:
- `--project <slug>` — project slug (default: `default`)
- `--database-url <url>` — database URL override
- `--dry-run` — print planned actions without writing

### Manual Key Creation

1. Open `http://localhost:8100` in a browser.
2. Click **API keys** (visible to admin-role actors only).
3. Create a key with the desired role.
4. Copy the key — it is shown only once.

Or via REST API with an existing admin key:

```bash
curl -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent", "role": "implementer", "description": "My agent"}'
```

## Running

### Direct

```bash
uvicorn flow_app.main:app --host 0.0.0.0 --port 8100
```

### Docker Compose

```bash
docker compose up -d
```

### systemd

See unit files below.

## Automation Runner

The runner is a separate process that runs a periodic pass consisting of:

1. **Dispatch** — for each configured agent profile, find the next capable unclaimed task and spawn a subprocess.
2. **Stale recovery** — find agent runs with no heartbeat beyond their timeout, release the task.
3. **Cron rules** — evaluate `cron`-trigger automation rules.
4. **Webhook delivery** — send pending webhook deliveries.

### CLI

```bash
# One pass
python -m flow_app.runner --once

# Stale recovery only
python -m flow_app.runner --once --stale-recovery-only

# Continuous loop
python -m flow_app.runner --profiles hermes-delegator --interval 30

# Dry run
python -m flow_app.runner --once --dry-run
```

### Runner Environment

Before enabling the runner, set `FLOW_API_KEY` to a valid implementer-role key. The runner shares the same `FLOW_DATABASE_URL` as the web service.

## systemd Services

### `flow-web.service`

```ini
[Unit]
Description=Flow web server
After=network.target

[Service]
Type=simple
User=flow
Group=flow
WorkingDirectory=/opt/flow
EnvironmentFile=/etc/axis/flow.env
ExecStart=/opt/flow/.venv/bin/python -m uvicorn flow_app.main:app --host 0.0.0.0 --port 8100
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### `flow-runner.service`

```ini
[Unit]
Description=Flow automation runner
After=network.target flow-web.service

[Service]
Type=simple
User=flow
Group=flow
WorkingDirectory=/opt/flow
EnvironmentFile=/etc/axis/flow.env
ExecStart=/opt/flow/.venv/bin/python -m flow_app.runner
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Shared Data

Both services must use the same SQLite database file. Set `FLOW_DATABASE_URL` to the same path in both environment files.

### Runner Environment File

```env
FLOW_RUNNER_PROFILES=hermes-delegator
FLOW_RUNNER_INTERVAL=30
FLOW_BASE_URL=http://127.0.0.1:8100
FLOW_API_KEY=
FLOW_DATABASE_URL=sqlite:////var/lib/flow/flow.sqlite
FLOW_RUNNER_DRY_RUN=false
```

### Smoke Test

```bash
cd /opt/flow
/opt/flow/.venv/bin/python -m flow_app.runner --once --stale-recovery-only
```

## Backup and Restore

### Backup

1. Stop Flow (systemctl, docker compose down, or Ctrl+C).
2. Copy the SQLite file:
   ```bash
   cp ./data/flow.sqlite ./data/flow.sqlite.bak.$(date +%Y%m%d)
   ```
3. Verify:
   ```bash
   sqlite3 ./data/flow.sqlite.bak.$(date +%Y%m%d) "PRAGMA integrity_check;"
   ```

### Restore

1. Stop Flow.
2. Replace the SQLite file:
   ```bash
   cp ./data/flow.sqlite.bak.20250115 ./data/flow.sqlite
   ```
3. Start Flow.
4. Verify:
   ```bash
   curl http://localhost:8100/healthz
   ```

## Schema Migration

Flow automatically migrates the schema on startup via `ensure_compatible_schema()` in `flow_app/migration.py`. This runs inside the application lifespan before any requests are served.

- New columns are added with `ALTER TABLE ADD COLUMN` and safe defaults.
- Migrations are one-way: columns are added but never removed.
- Existing rows get default values for new columns.
- Rolling back to an older Flow version is safe — older versions ignore unknown columns.

## Docker Compose

The `docker-compose.yml` defines two services:

| Service | Profile | Purpose |
|---------|---------|---------|
| `flow` | default | Web server on port 8100 |
| `flow-runner` | `runner` | Automation runner (opt-in) |

Both share the `flow-data` volume mounted at `/data`.

The Dockerfile uses `python:3.12-slim`, installs the package with `pip install .`, and runs uvicorn.

## Themes

Flow ships two themes: `neutral` (default) and `axis_love`. Set via `FLOW_THEME`. Users can switch at runtime in the browser; preference is stored in `localStorage`.

## Trusted Headers

Set `FLOW_TRUSTED_HEADERS=true` only when deploying behind a reverse proxy that strips all inbound `X-Axis-*` headers before setting its own. Otherwise, any client can spoof admin access.

## Session Cookies

Set `FLOW_SESSION_SECRET` to enable browser session cookie authentication. The session cookie (`flow_session`) is HMAC-SHA256 signed with a 12-hour expiry. Set `FLOW_SESSION_COOKIE_SECURE=true` in production (HTTPS).

## Common Mistakes

- **Using the public URL for `FLOW_BASE_URL`** — Cloudflare or other proxies may reject non-browser clients. Use an internal URL like `http://127.0.0.1:8100`.
- **Not setting `FLOW_API_KEY` before enabling the runner** — the runner needs a valid implementer-role key to dispatch agents.
- **Sharing admin keys with agents** — use the minimum role needed. Admin keys can create and revoke other keys.
- **Forgetting to save API keys on creation** — the raw key is shown only once.
- **Enabling `FLOW_TRUSTED_HEADERS` without a stripping proxy** — any client can send `X-Axis-Admin: 1` and get admin access.

## Verification Checklist

After any deployment, migration, or restore:

1. `curl http://localhost:8100/healthz` → `{"ok": true, "database": true}`
2. `curl -H "Authorization: Bearer <key>" http://localhost:8100/api/tasks` → task list or `[]`
3. `sqlite3 ./data/flow.sqlite "SELECT count(*) FROM tasks;"` → expected count
4. `sqlite3 ./data/flow.sqlite "SELECT count(*) FROM api_keys WHERE revoked_at IS NULL;"` → expected count

## See Also

- [Architecture](Architecture.md) — system design and data model
- [Security](Modules/Security.md) — roles and permissions
- [Dispatcher](Modules/Dispatcher.md) — agent dispatch and runner details
- [Webhooks](Modules/Webhooks.md) — webhook configuration and delivery
