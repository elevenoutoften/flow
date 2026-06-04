# Webhooks

## Source

| File | Role |
|------|------|
| `flow_app/webhooks.py` | Event emission, delivery logic |
| `flow_app/webhook_cli.py` | CLI delivery runner |
| `flow_app/ssrf.py` | URL validation, IP pinning |
| `flow_app/models.py:223-279` | `WebhookConfig`, `WebhookDelivery`, `NotificationDelivery` models |
| `flow_app/routes/webhooks.py` | REST API router |

## Overview

Flow webhooks create delivery records when task and idea events happen. A separate runner sends pending deliveries to configured URLs with HMAC-SHA256 signatures.

## Event Types

| Event | When |
|-------|------|
| `task_created` | After a task is created |
| `task_claimed` | After a task is claimed |
| `task_moved` | After a task changes status |
| `task_completed` | After a task is marked done |
| `task_blocked` | When `human_required` changes to true |
| `idea_promoted` | For each task created while promoting an idea |

## Webhook Payload

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

## Signature Verification

Each delivery includes:
- `X-Flow-Event`: event name
- `X-Flow-Delivery-ID`: delivery ID
- `X-Flow-Signature`: lowercase hex HMAC-SHA256 of the raw request body using the webhook secret

```python
import hashlib, hmac

def verify(secret: str, body: bytes, signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Use the exact raw request body bytes. Re-serializing JSON can change the signature.

## Secret Storage and Encryption

When `FLOW_WEBHOOK_ENCRYPTION_KEY` is set to a valid Fernet key, Flow encrypts webhook signing secrets at rest using Fernet (AES-128-CBC + HMAC-SHA256).

- `secret_encrypted=1` means the stored `secret` is Fernet ciphertext.
- `secret_encrypted=0` means plaintext (legacy behavior, default without key).
- The key is held in the environment variable, not in the database.
- API responses never include `secret` except on creation.

### Key Rotation

```bash
python -m flow_app.webhook_cli rotate-key --old-key <OLD> --new-key <NEW>
python -m flow_app.webhook_cli rotate-key --old-key <OLD> --new-key <NEW> --dry-run
```

### Plaintext to Encrypted Migration

```bash
python -m flow_app.webhook_cli re-encrypt
```

## SSRF Protection

Webhook URLs are validated at create/update time and re-validated at delivery time:

- Only `http://` and `https://` schemes.
- Hostnames must resolve to global unicast (public) IP addresses.
- Rejects: localhost, RFC 1918, link-local, CGNAT, IPv6 ULA, multicast, reserved, cloud metadata.
- IPv4-mapped IPv6 addresses are always rejected.
- At delivery, the resolved IP is pinned in the HTTP transport to prevent DNS rebinding.
- HTTPS preserves original hostname for SNI and certificate verification.

## Retry Behavior

- Success: 2xx response → `status=success`.
- Failure: non-2xx or transport error → `status=retrying`.
- Backoff: `retry_backoff_seconds * 2^(attempts - 1)`.
- After `max_retries` → `status=failed`.

## Payload and Response Caps

- `FLOW_MAX_WEBHOOK_PAYLOAD_BYTES` (default 65536) caps stored payloads.
- `FLOW_MAX_WEBHOOK_RESPONSE_BYTES` (default 4096) caps stored response bodies, truncated with `...[truncated]`.

## Delivery Log

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/webhooks/{id}/deliveries` | List deliveries (filter by `status`) |
| `GET` | `/api/webhooks/{id}/deliveries/{did}` | Get delivery with payload |
| `POST` | `/api/webhooks/{id}/deliveries/{did}/retry` | Retry failed delivery |

## CLI Delivery Runner

```bash
python -m flow_app.webhook_cli deliver                    # One pass
python -m flow_app.webhook_cli deliver --loop --interval 30  # Continuous
python -m flow_app.webhook_cli deliver --dry-run          # Preview
python -m flow_app.webhook_cli cleanup-deliveries         # Purge old deliveries
python -m flow_app.webhook_cli cleanup-deliveries --days 14 --dry-run
```

Delivery retention: `FLOW_MAX_WEBHOOK_DELIVERY_AGE_DAYS` (default 30).

## REST API

| Method | Path | Permission |
|--------|------|-----------|
| `POST` | `/api/webhooks` | `webhook:manage` |
| `GET` | `/api/webhooks` | `webhook:read` |
| `GET` | `/api/webhooks/{id}` | `webhook:read` |
| `PATCH` | `/api/webhooks/{id}` | `webhook:manage` |
| `DELETE` | `/api/webhooks/{id}` | `webhook:manage` |

## See Also

- [Automation Rules](AutomationRules.md) — event triggers
- [Security](Security.md) — webhook permissions
- [Operations](../Operations.md) — deployment configuration
