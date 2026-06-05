"""CLI for importing Flow adapter templates through the REST API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


OVERRIDE_FIELDS = (
    "description",
    "agent_type",
    "capabilities",
    "command",
    "command_allowlist",
    "env_allowlist",
    "working_directory",
    "dispatch_statuses",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List, preview, or import built-in Flow adapter templates.")
    parser.add_argument("template", nargs="?", help="Built-in adapter template name, such as hermes or codex.")
    parser.add_argument("--base-url", default=os.environ.get("FLOW_BASE_URL", "http://localhost:8100"))
    parser.add_argument("--api-key", default=os.environ.get("FLOW_API_KEY"))
    parser.add_argument("--list", action="store_true", help="List available adapter templates.")
    parser.add_argument("--preview", action="store_true", help="Preview the template import without persisting it.")
    parser.add_argument("--name", help="Name for the created or previewed agent.")
    parser.add_argument("--on-collision", choices=("error", "skip", "update"), default="error")
    parser.add_argument("--enabled", choices=("true", "false"), help="Override whether the agent is enabled.")
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--heartbeat-timeout-seconds", type=int)
    parser.add_argument("--stale-claim-timeout-seconds", type=int)
    for field in OVERRIDE_FIELDS:
        parser.add_argument(f"--{field.replace('_', '-')}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    base_url = args.base_url.rstrip("/")

    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
            if args.list:
                response = client.get("/api/adapter-templates")
                response.raise_for_status()
                for template in response.json():
                    print(f"{template['name']}\t{template['family']}\t{template['description']}")
                return 0

            if not args.template:
                parser.error("template is required unless --list is used")
            if not args.name:
                parser.error("--name is required unless --list is used")

            payload = _payload_from_args(args)
            endpoint = "preview" if args.preview else "instantiate"
            response = client.post(f"/api/adapter-templates/{args.template}/{endpoint}", json=payload)
            response.raise_for_status()
            print(json.dumps(response.json(), indent=2, sort_keys=True))
            return 0
    except httpx.HTTPStatusError as exc:
        print(f"Flow API returned {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
    except httpx.HTTPError as exc:
        print(f"Flow API request failed: {exc}", file=sys.stderr)
    return 1


def _payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": args.name}
    if not args.preview:
        payload["on_collision"] = args.on_collision
    if args.enabled is not None:
        payload["enabled"] = args.enabled == "true"
    for field in (
        *OVERRIDE_FIELDS,
        "max_concurrency",
        "heartbeat_timeout_seconds",
        "stale_claim_timeout_seconds",
    ):
        value = getattr(args, field)
        if value is not None:
            payload[field] = value
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
