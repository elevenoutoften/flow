# SQLite Concurrency

Flow enables SQLite WAL mode by default for SQLite database URLs.

## Defaults

- `FLOW_SQLITE_JOURNAL_MODE=WAL`
- `FLOW_SQLITE_BUSY_TIMEOUT_MS=5000`

On each new SQLite connection, Flow applies:

- `PRAGMA journal_mode=<configured mode>`
- `PRAGMA busy_timeout=<configured timeout>`

WAL mode allows readers to continue while a writer is committing, which removes the usual reader-writer lock contention seen with SQLite's default rollback journal.

## Operational Limits

WAL does not make SQLite a multi-writer database.

- SQLite still permits only one active writer at a time.
- Concurrent write transactions still serialize.
- A short `busy_timeout` helps brief lock contention succeed instead of failing immediately, but it does not remove write serialization.

## Deployment Guidance

- Use SQLite for single-instance or otherwise low-write deployments.
- Use PostgreSQL for deployments with multiple writer processes, high write volume, or HA requirements.

The app has background writer activity from runner and dispatcher paths. WAL improves the default posture for SQLite, but it is still not a replacement for a server database when multiple writers are expected to compete regularly.

## Configuration

- `FLOW_SQLITE_JOURNAL_MODE`
  Accepted values: `WAL`, `DELETE`, `TRUNCATE`, `PERSIST`, `MEMORY`, `OFF`
- `FLOW_SQLITE_BUSY_TIMEOUT_MS`
  Integer timeout in milliseconds. Default: `5000`

Example:

```bash
export FLOW_SQLITE_JOURNAL_MODE=WAL
export FLOW_SQLITE_BUSY_TIMEOUT_MS=5000
```
