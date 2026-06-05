# Runner Security

## Runner Authentication

Runners authenticate to polling and lease endpoints with the standard HTTP bearer header:

```http
Authorization: Bearer <api_key>
```

Runner management APIs use scoped Flow API key permissions. `RUNNER_READ` covers reading runner state, polling, and lease heartbeats. `RUNNER_MANAGE` covers creating runners and updating runner configuration.

## Secret References in `api_key_ref`

Runner API keys are configured through `api_key_ref`. The field supports these forms:

- `env:ENV_VAR_NAME` resolves the secret from the server environment at runtime.
- `file:/path/to/secret` resolves the secret from a file at runtime, restricted to configured allowed roots.
- Plaintext values are stored as provided for compatibility, but are redacted to `***` in all API responses.

Prefer `env:` or `file:` references for production deployments so runner credentials are not stored directly in the database.

## Network Boundaries

Runners should communicate with Flow over TLS. Flow does not terminate or enforce TLS itself, so production deployments should place the server behind a reverse proxy that provides HTTPS.

Runners poll Flow for work. They do not need inbound ports exposed on the runner side.

## Lease Security

Runner leases expire after `lease_duration_seconds`. Heartbeats and completion requests must come from the same runner that holds the lease.

Stale lease recovery marks expired leases as expired, marks the associated run stale, returns the task to the queue, and records an audit note.

## Agent Concurrency

Runner polling respects `Agent.max_concurrency`. A runner cannot lease more tasks for an agent than the agent concurrency limit, even when the runner's own `max_concurrent_leases` is higher.

## Dispatch Readiness

Runner polling uses the same dispatch readiness check as local dispatch. Tasks with unresolved `blocks` or `depends_on` dependencies are not leased until every blocking parent task is `done`.
