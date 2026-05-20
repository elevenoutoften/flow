# Flow Live Upgrade and Key-Rotation Runbook (F-32)

**Flow version:** 0.1.0
**Target:** Upgrade from pre-role Axis Love deployment to public Flow 0.1.0

> This runbook is self-contained. No need to read the planning conversation.

## Notable Changes in 0.1.0

| Change | Before | After |
|--------|--------|-------|
| Package name | `axis-flow` | `flow` |
| Default project | `legacy-project` | `default` |
| Theme | Axis Love (pink) | Neutral (via `FLOW_THEME=neutral`) |
| API keys | Unscoped, all-write | Role-scoped (`admin`, `architect`, `implementer`, `reviewer`, `read_only`) |

The `FLOW_THEME` env var defaults to `"neutral"`. The UI will look different from the pink Axis Love theme but has identical functionality.

---

## 1. Prerequisites

### 1.1 Stop Flow

Quiesce the running service so no writes are in progress.

```bash
# If running as a systemd service
sudo systemctl stop flow.service

# If running in Docker
docker compose down

# If running directly
# Press Ctrl+C in the terminal
```

### 1.2 Backup SQLite

```bash
BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
cp ./data/flow.sqlite "./data/flow.sqlite.bak.${BACKUP_DATE}"
```

### 1.3 Verify Backup Integrity

```bash
# Check task count matches
sqlite3 ./data/flow.sqlite "SELECT count(*) FROM tasks;"
sqlite3 "./data/flow.sqlite.bak.${BACKUP_DATE}" "SELECT count(*) FROM tasks;"

# Integrity check — must return "ok"
sqlite3 "./data/flow.sqlite.bak.${BACKUP_DATE}" "PRAGMA integrity_check;"
```

Both counts must match and integrity check must return `ok`. If not, **STOP** and investigate before proceeding.

### 1.4 Record Current State

```bash
# Count existing API keys (these will become read_only after migration)
sqlite3 ./data/flow.sqlite "SELECT count(*) FROM api_keys WHERE revoked_at IS NULL;"

# List existing key names for reference
sqlite3 ./data/flow.sqlite "SELECT id, name FROM api_keys WHERE revoked_at IS NULL;"
```

Save this output — you will need it during key rotation.

---

## 2. Staging Rehearsal

**Do not skip this step.** Rehearse the upgrade against a copy of the production database.

### 2.1 Copy DB to Staging Location

```bash
mkdir -p /tmp/flow-staging
cp "./data/flow.sqlite.bak.${BACKUP_DATE}" /tmp/flow-staging/flow.sqlite
```

### 2.2 Deploy Upgraded Service Against Staging DB

```bash
cd /path/to/flow-0.1.0
FLOW_DATA_DIR=/tmp/flow-staging uvicorn flow_app.main:app --host 127.0.0.1 --port 8199 &
STAGING_PID=$!
```

### 2.3 Smoke Test Staging

```bash
# Health check
curl -s http://127.0.0.1:8199/healthz
# Expected: {"ok": true, "database": true}

# Read tasks
curl -s http://127.0.0.1:8199/api/tasks
# Expected: [] or list of tasks (unscoped keys are now read_only)

# Board UI loads
curl -s http://127.0.0.1:8199/ | head -20
# Expected: HTML with board content
```

### 2.4 Tear Down Staging

```bash
kill $STAGING_PID
rm -rf /tmp/flow-staging
```

If any staging test fails, **do not proceed** to live upgrade. Debug and re-rehearse.

---

## 3. Live Upgrade Steps

### 3.1 Quiesce

Ensure the old service is fully stopped (done in Prerequisites step 1.1). Verify no process is holding the database:

```bash
lsof ./data/flow.sqlite 2>/dev/null
# Should return nothing
```

### 3.2 Deploy

Replace the old service binary/code with Flow 0.1.0.

```bash
# If using git
cd /path/to/flow
git checkout v0.1.0  # or the appropriate branch/tag

# If using Docker
docker compose pull
```

### 3.3 Start Service

```bash
# systemd
sudo systemctl start flow.service

# Docker
docker compose up -d

# Direct
FLOW_DATA_DIR=./data uvicorn flow_app.main:app --host 0.0.0.0 --port 8100 &
```

### 3.4 Healthcheck

```bash
curl -s http://localhost:8100/healthz
# Expected: {"ok": true, "database": true}
```

If healthcheck fails, go to [Rollback](#5-rollback).

### 3.5 REST API Smoke Test

```bash
# Read tasks (should work with any existing key — they are now read_only)
curl -s -H "Authorization: Bearer <existing-key>" http://localhost:8100/api/tasks

# Attempt write — should fail with 403 (old keys are read_only)
curl -s -X POST http://localhost:8100/api/tasks \
  -H "Authorization: Bearer <existing-key>" \
  -H "Content-Type: application/json" \
  -d '{"title": "smoke test", "project": "default"}'
# Expected: 403 Forbidden (key has read_only role)
```

### 3.6 MCP Smoke Test

If MCP is enabled, verify MCP tools respond:

```bash
# Test MCP read tool (e.g., list_tasks)
curl -s -X POST http://localhost:8100/mcp \
  -H "Authorization: Bearer <existing-key>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
# Expected: tools list response
```

---

## 4. Key Rotation

### Critical: Old Unscoped Keys Are Now Read-Only

During schema migration, all pre-existing API keys that did not have a role were assigned the default role `read_only`. These keys can **only** read tasks and view the board. They **cannot** create, edit, claim, move, or mutate any state.

**Before any write agents (codex, hermes, etc.) can resume work, an admin MUST create new role-scoped keys.**

### 4.1 Create Admin Key (First Step)

You need at least one admin key to manage other keys. If you have an existing key, use it to create new keys. If all existing keys are read_only and you have no admin access, you will need to insert an admin key directly into the database:

```bash
# Option A: If you have any working admin key
ADMIN_KEY="<existing-admin-key>"

# Option B: Insert admin key directly into SQLite (emergency only)
python3 -c "
import sqlite3, secrets, time
db = sqlite3.connect('./data/flow.sqlite')
key = 'flow_sk_' + secrets.token_hex(24)
key_id = f'key_{int(time.time())}'
db.execute(
    'INSERT INTO api_keys (id, name, role, api_key, created_at) VALUES (?, ?, ?, ?, ?)',
    (key_id, 'emergency-admin', 'admin', key, time.strftime('%Y-%m-%dT%H:%M:%S'))
)
db.commit()
print(f'Created admin key: {key}')
print(f'Key ID: {key_id}')
"
ADMIN_KEY="<key-from-output-above>"
```

### 4.2 Create Role-Scoped Keys

Run these commands using the admin key from step 4.1.

#### Admin Key

```bash
curl -s -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer ${ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "admin", "role": "admin", "description": "Admin key for key management"}'
```

Save the `api_key` from the response — it is shown only once.

#### Architect Key

```bash
curl -s -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer ${ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "hermes", "role": "architect", "description": "Hermes planning agent"}'
```

Save the `api_key` from the response.

#### Implementer Key

```bash
curl -s -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer ${ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "codex", "role": "implementer", "description": "Codex feature agent"}'
```

Save the `api_key` from the response.

#### Reviewer Key

```bash
curl -s -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer ${ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "reviewer", "role": "reviewer", "description": "Review agent"}'
```

Save the `api_key` from the response.

### 4.3 Update Agent Configurations

Replace the old API keys in all agent configurations (codex, hermes, CI/CD, etc.) with the new role-scoped keys created above.

### 4.4 Revoke Old Unscoped Keys (Optional but Recommended)

Once all agents are using new keys, revoke the old read_only keys:

```bash
# List all keys to find old ones
curl -s -H "Authorization: Bearer ${ADMIN_KEY}" http://localhost:8100/api/api-keys

# Revoke each old key by ID
curl -s -X POST http://localhost:8100/api/api-keys/key_XXXXXX/revoke \
  -H "Authorization: Bearer ${ADMIN_KEY}"
```

---

## 5. Rollback

If the upgrade fails at any point, restore from the verified backup.

### 5.1 Stop New Service

```bash
sudo systemctl stop flow.service
# or
docker compose down
# or kill the direct process
```

### 5.2 Restore Backup

```bash
# Use the backup created in Prerequisites
cp "./data/flow.sqlite.bak.${BACKUP_DATE}" ./data/flow.sqlite
```

### 5.3 Verify Restore

```bash
sqlite3 ./data/flow.sqlite "PRAGMA integrity_check;"
# Expected: ok

sqlite3 ./data/flow.sqlite "SELECT count(*) FROM tasks;"
# Should match the count recorded in Prerequisites
```

### 5.4 Start Old Service

```bash
# Deploy the pre-upgrade version
cd /path/to/old-flow-version

# Start
sudo systemctl start flow.service
# or
docker compose up -d
```

### 5.5 Verify Rollback

```bash
curl -s http://localhost:8100/healthz
# Expected: {"ok": true, "database": true}

curl -s -H "Authorization: Bearer <existing-key>" http://localhost:8100/api/tasks
# Expected: tasks list (old keys still have full write access on old version)
```

> **Note:** The schema migration is one-way — new columns added during the upgrade will remain in the database. The old Flow version will ignore unknown columns. This is safe and will not cause data loss.

---

## 6. Post-Upgrade Verification

### 6.1 Board UI Loads

Open `http://localhost:8100` in a browser. The board should display with the neutral theme (not pink). Verify:

- Task columns render (backlog, todo, doing, review, done)
- Tasks are visible
- API keys section is visible (only when authenticated with an admin key)

### 6.2 API Reads Work

```bash
curl -s -H "Authorization: Bearer <new-read-only-key>" http://localhost:8100/api/tasks
# Expected: list of tasks or []

curl -s -H "Authorization: Bearer <new-read-only-key>" http://localhost:8100/api/tasks/task_000001
# Expected: task details or 404 if task doesn't exist
```

### 6.3 API Writes Work with New Keys

```bash
# Architect can create tasks
curl -s -X POST http://localhost:8100/api/tasks \
  -H "Authorization: Bearer <new-architect-key>" \
  -H "Content-Type: application/json" \
  -d '{"title": "verify write access", "project": "default"}'
# Expected: 201 Created with task object

# Implementer can claim tasks
curl -s -X POST http://localhost:8100/api/tasks/task_XXXXXX/claim \
  -H "Authorization: Bearer <new-implementer-key>"
# Expected: 200 with updated task

# Read-only key CANNOT write (should get 403)
curl -s -X POST http://localhost:8100/api/tasks \
  -H "Authorization: Bearer <new-read-only-key>" \
  -H "Content-Type: application/json" \
  -d '{"title": "should fail", "project": "default"}'
# Expected: 403 Forbidden
```

### 6.4 MCP Tools Work

```bash
# List available MCP tools
curl -s -X POST http://localhost:8100/mcp \
  -H "Authorization: Bearer <new-architect-key>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
# Expected: list of 12 MCP tools

# Test a write tool (create_task) with architect key
curl -s -X POST http://localhost:8100/mcp \
  -H "Authorization: Bearer <new-architect-key>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "create_task", "arguments": {"title": "mcp write test", "project": "default"}}}'
# Expected: 200 with created task

# Test write tool with read_only key (should fail)
curl -s -X POST http://localhost:8100/mcp \
  -H "Authorization: Bearer <new-read-only-key>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "create_task", "arguments": {"title": "should fail", "project": "default"}}}'
# Expected: error / 403
```

### 6.5 Final Checks

- [ ] Health check returns 200
- [ ] Board UI loads with neutral theme
- [ ] API reads work with all key roles
- [ ] API writes work with admin, architect, implementer, reviewer keys
- [ ] API writes are blocked for read_only keys (403)
- [ ] MCP tools work with appropriate role keys
- [ ] MCP write tools are blocked for read_only keys
- [ ] All agent configurations updated with new role-scoped keys
- [ ] Old unscoped keys revoked (optional)

---

## Quick Reference

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/healthz` | GET | None | Health check |
| `/api/tasks` | GET | Bearer | List tasks |
| `/api/tasks` | POST | Bearer (architect+) | Create task |
| `/api/api-keys` | POST | Bearer (admin) | Create API key |
| `/api/api-keys/{id}/revoke` | POST | Bearer (admin) | Revoke API key |

| Env Var | Default | Description |
|---------|---------|-------------|
| `FLOW_DATA_DIR` | `./data` | Data directory |
| `FLOW_DATABASE_URL` | `sqlite:///{data_dir}/flow.sqlite` | Database URL |
| `FLOW_THEME` | `neutral` | UI theme |
| `FLOW_PORT` | `8100` | HTTP port |
