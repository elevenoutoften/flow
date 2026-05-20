# Flow Migration and Backup Notes

## Data Location

Flow stores all data in a single SQLite database file.

| Setting | Default | Description |
|---------|---------|-------------|
| `FLOW_DATA_DIR` | `./data` | Directory for data files |
| `FLOW_DATABASE_URL` | `sqlite:///{data_dir}/flow.sqlite` | Full database connection URL |

By default, the database is at `./data/flow.sqlite` relative to the working directory.

To use a custom location:

```bash
FLOW_DATA_DIR=/var/lib/flow uvicorn flow_app.main:app
```

Or set the database URL directly:

```bash
FLOW_DATABASE_URL=sqlite:////var/lib/flow/flow.sqlite uvicorn flow_app.main:app
```

## Backup

1. **Stop Flow** — ensure no writes are in progress

   ```bash
   # If running as a service
   sudo systemctl stop flow.service

   # If running in Docker
   docker compose down

   # If running directly
   # Press Ctrl+C in the terminal
   ```

2. **Copy the SQLite file**

   ```bash
   cp ./data/flow.sqlite ./data/flow.sqlite.bak.$(date +%Y%m%d)
   ```

3. **Verify the backup**

   ```bash
   sqlite3 ./data/flow.sqlite.bak.$(date +%Y%m%d) "SELECT count(*) FROM tasks;"
   sqlite3 ./data/flow.sqlite.bak.$(date +%Y%m%d) "PRAGMA integrity_check;"
   ```

   The integrity check should return `ok`.

## Restore

1. **Stop Flow**

   ```bash
   sudo systemctl stop flow.service
   ```

2. **Replace the SQLite file**

   ```bash
   cp ./data/flow.sqlite.bak.20250115 ./data/flow.sqlite
   ```

3. **Start Flow**

   ```bash
   sudo systemctl start flow.service
   ```

4. **Verify the restore**

   ```bash
   curl http://localhost:8100/healthz
   ```

   Expect: `{"ok": true, "database": true}`

   Then make a read-only API call:

   ```bash
   curl -H "Authorization: Bearer <your-key>" http://localhost:8100/api/tasks
   ```

## Schema Migration

Flow automatically migrates the database schema on startup via `ensure_compatible_schema()`. This runs inside the application lifespan, before any requests are served.

### How It Works

On startup, Flow inspects the existing database tables and columns. If any expected columns are missing, it adds them with safe defaults using `ALTER TABLE ADD COLUMN`.

Columns added automatically:

| Column | Type | Default |
|--------|------|---------|
| `source_filename` | VARCHAR(500) | NULL |
| `source_line` | INTEGER | NULL |
| `import_batch_id` | VARCHAR(64) | NULL |
| `source_title` | VARCHAR(240) | NULL |
| `human_required` | INTEGER | 0 (false) |
| `assignee_type` | VARCHAR(24) | `'agent'` |
| `blocker_reason` | TEXT | `''` (empty) |
| `complexity` | VARCHAR(24) | `'small'` |
| `impact` | VARCHAR(24) | `'medium'` |
| `effort` | VARCHAR(24) | `'medium'` |
| `risk` | VARCHAR(24) | `'low'` |
| `role` (on `api_keys`) | VARCHAR(32) | `'read_only'` |

### One-Way Migrations

Schema migrations are one-way: new columns are added but never removed. This means:

- **Upgrading is safe** — existing data is preserved, new columns get safe defaults
- **All existing rows** get default values for new columns
- **No data loss** — `ALTER TABLE ADD COLUMN` does not modify existing data

### Rolling Back Limitations

If you need to roll back to an older version of Flow:

- **New columns will remain** in the database — older Flow versions will ignore them
- **New default values are safe** — older versions will not see or use the new columns
- **Data in new columns is preserved** — if you upgrade again later, the data will still be there
- **Do not downgrade if** the older version cannot handle the presence of unknown columns (Flow handles this gracefully, but third-party tools may not)

The only scenario where rollback could cause issues is if you manually populate new columns with data that the older version's business logic would not expect. In practice, this is unlikely to cause problems since Flow ignores unknown columns.

## Verification Checklist

After any migration, backup, or restore operation:

1. **Health check**

   ```bash
   curl http://localhost:8100/healthz
   # Expected: {"ok": true, "database": true}
   ```

2. **Read-only API call**

   ```bash
   curl -H "Authorization: Bearer <your-key>" http://localhost:8100/api/tasks
   # Expected: [] or list of tasks
   ```

3. **Check task count**

   ```bash
   sqlite3 ./data/flow.sqlite "SELECT count(*) FROM tasks;"
   ```

4. **Check API key count**

   ```bash
   sqlite3 ./data/flow.sqlite "SELECT count(*) FROM api_keys WHERE revoked_at IS NULL;"
   ```

5. **Verify schema columns** (after upgrade)

   ```bash
   sqlite3 ./data/flow.sqlite ".schema tasks"
   ```

   Confirm all expected columns are present.
