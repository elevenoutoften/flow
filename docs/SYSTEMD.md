# Flow systemd services

These examples run the web server and automation runner from an installed checkout in `/opt/flow` with configuration in `/etc/axis/flow.env`.

## `flow-web.service`

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

## `flow-runner.service`

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

## Shared data

The web and runner services must use the same SQLite database file. With Docker, both services share the `flow-data` volume. With systemd, both services need the same `FLOW_DATABASE_URL` pointing to the same filesystem path, for example:

```env
FLOW_DATABASE_URL=sqlite:////var/lib/flow/flow.sqlite
FLOW_DATA_DIR=/var/lib/flow
```

## Runner environment

Set these values in `/etc/axis/flow.env` for the runner:

```env
FLOW_RUNNER_PROFILES=hermes-delegator
FLOW_RUNNER_INTERVAL=30
FLOW_BASE_URL=http://127.0.0.1:8100
FLOW_API_KEY=
FLOW_DATABASE_URL=sqlite:////var/lib/flow/flow.sqlite
FLOW_RUNNER_DRY_RUN=false
```

`FLOW_RUNNER_PROFILES` is a comma-separated list of agent profile names. `FLOW_RUNNER_INTERVAL` is the number of seconds between runner passes. `FLOW_BASE_URL` should point to the Flow web server. `FLOW_API_KEY` must be an admin or implementer-scoped key. `FLOW_RUNNER_DRY_RUN=true` logs planned work without executing it.

## Quick smoke test

Run one stale-recovery pass before enabling the long-running service:

```bash
cd /opt/flow
/opt/flow/.venv/bin/python -m flow_app.runner --once --stale-recovery-only
```

## Checking runner health

```bash
systemctl status flow-runner
journalctl -u flow-runner
```
