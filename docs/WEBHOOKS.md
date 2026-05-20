# Webhooks

Flow webhooks create delivery records when task and idea events happen. A separate runner sends pending deliveries to configured URLs.

## Notification Provider Boundary

Webhooks emit HTTP `POST` notifications for task and idea lifecycle events. They are an outbound integration mechanism: Flow records a webhook delivery, signs the JSON payload, and sends it to the configured URL.

The `NotificationProvider` interface in `flow_app/notifications.py` is the boundary for future notification channels such as email, Slack, or in-app notifications. Currently only `WebhookNotificationProvider` implements that interface.

This separation is intentional. Webhook delivery is not the same as an in-app notification provider, even though both can be triggered by the same lifecycle events.

## Webhook Configuration

Create a webhook:

```bash
curl -X POST http://localhost:8000/api/webhooks \
  -H 'Content-Type: application/json' \
  # Requires FLOW_TRUSTED_HEADERS=true
  -H 'X-Axis-Admin: 1' \
  -d '{
    "name": "Task events",
    "url": "https://example.com/flow-webhook",
    "events": ["task_created", "task_completed"],
    "project": "*",
    "max_retries": 3,
    "retry_backoff_seconds": 60
  }'
```

The create response includes `secret` once. Store it securely; later reads do not return it.

Update or disable a webhook with `PATCH /api/webhooks/{webhook_id}`:

```json
{
  "name": "Production task events",
  "url": "https://example.com/new-target",
  "events": ["task_created", "task_moved"],
  "active": 0
}
```

Set `active` to `1` to enable delivery creation again. Set `project` to a project slug for project-scoped events, or `*` for all projects.

## Event Types

All webhook payloads are JSON objects with this shape:

```json
{
  "event": "task_created",
  "event_id": "uuid",
  "timestamp": "2026-05-18T12:00:00+00:00",
  "project": "default",
  "task_id": "flow_000001",
  "task_title": "Implement feature",
  "changes": {}
}
```

Supported events:

- `task_created`: emitted after a task is created.
- `task_claimed`: emitted after a task is claimed. `changes` includes assignee and status transition.
- `task_moved`: emitted after a task changes status. `changes.status` includes `from` and `to`.
- `task_completed`: emitted after a task is marked done. `changes.status` includes `from` and `to`.
- `task_blocked`: emitted when `human_required` changes from false to true. `changes` includes `human_required` and `blocker_reason`.
- `idea_promoted`: emitted once for each task created while promoting an idea. `changes.idea_id` identifies the source idea.

## Signature Verification

Each delivery includes these headers:

- `X-Flow-Event`: event name.
- `X-Flow-Delivery-ID`: delivery id.
- `X-Flow-Signature`: lowercase hex HMAC-SHA256 of the raw request body using the webhook secret.

Python verification example:

```python
import hashlib
import hmac

def verify(secret: str, body: bytes, signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Use the exact raw request body bytes. Re-serializing JSON can change the signature.

## Retry Behavior

Successful HTTP status codes are `2xx`. Any non-`2xx` response or `httpx` transport error records a failure.

Failed deliveries retry until `max_retries` is reached. While retries remain, the delivery status is `retrying` and `next_attempt_at` is set with exponential backoff:

```text
retry_backoff_seconds * 2^(attempts - 1)
```

When attempts reach `max_retries`, the delivery status becomes `failed`.

## Delivery Log

List deliveries for a webhook:

```bash
curl \
  # Requires FLOW_TRUSTED_HEADERS=true
  -H 'X-Axis-Admin: 1' \
  http://localhost:8000/api/webhooks/webhook_000001/deliveries
```

Filter by status with `?status=pending`, `success`, `retrying`, or `failed`.

Read a delivery with payload:

```bash
curl \
  # Requires FLOW_TRUSTED_HEADERS=true
  -H 'X-Axis-Admin: 1' \
  http://localhost:8000/api/webhooks/webhook_000001/deliveries/delivery_000001
```

Retry a failed delivery immediately:

```bash
curl -X POST \
  # Requires FLOW_TRUSTED_HEADERS=true
  -H 'X-Axis-Admin: 1' \
  http://localhost:8000/api/webhooks/webhook_000001/deliveries/delivery_000001/retry
```

## API Reference

`POST /api/webhooks`

Request body:

```json
{
  "name": "Task events",
  "url": "https://example.com/flow-webhook",
  "events": ["task_created"],
  "project": "*",
  "max_retries": 3,
  "retry_backoff_seconds": 60
}
```

Response: `201` with webhook config plus `secret`.

`GET /api/webhooks`

Optional query: `project=default`. Response: list of webhook configs without secrets.

`GET /api/webhooks/{webhook_id}`

Response: one webhook config without secret.

`PATCH /api/webhooks/{webhook_id}`

Request body may include `name`, `url`, `events`, `active`, `max_retries`, `retry_backoff_seconds`, and `project`.

`DELETE /api/webhooks/{webhook_id}`

Deletes the webhook and its deliveries. Response: `204`.

`GET /api/webhooks/{webhook_id}/deliveries`

Optional query: `status=pending|success|retrying|failed`. Response: delivery metadata without payload.

`GET /api/webhooks/{webhook_id}/deliveries/{delivery_id}`

Response: delivery metadata plus the serialized `payload` string.

`POST /api/webhooks/{webhook_id}/deliveries/{delivery_id}/retry`

Retries a failed delivery immediately and returns:

```json
{
  "id": "delivery_000001",
  "status": "success",
  "message": "Delivery retry finished with status success."
}
```

## CLI Delivery Runner

Run one delivery pass:

```bash
python -m flow_app.webhook_cli deliver
```

Run continuously:

```bash
python -m flow_app.webhook_cli deliver --loop --interval 30
```

Preview ready deliveries without sending HTTP requests:

```bash
python -m flow_app.webhook_cli deliver --dry-run
```

The task-specific test command is:

```bash
uv run --extra test python -m pytest tests/test_webhooks.py -v
```
