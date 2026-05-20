"""CLI runner for processing pending webhook deliveries."""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import select

from .database import build_engine, build_session_factory, default_database_url
from .main import ensure_compatible_schema
from .models import WebhookDelivery, utcnow
from .repository import get_webhook_config
from .webhooks import deliver_webhook


def run_deliveries(dry_run: bool = False) -> int:
    """Process all pending/ready deliveries. Returns count of deliveries processed."""
    engine = build_engine(default_database_url())
    ensure_compatible_schema(engine)
    SessionLocal = build_session_factory(engine)
    db = SessionLocal()
    try:
        now = utcnow()
        stmt = (
            select(WebhookDelivery)
            .where(WebhookDelivery.status.in_(["pending", "retrying"]))
            .where((WebhookDelivery.next_attempt_at == None) | (WebhookDelivery.next_attempt_at <= now))
        )
        deliveries = list(db.scalars(stmt).all())
        processed = 0
        for delivery in deliveries:
            config = get_webhook_config(db, delivery.webhook_id)
            if config is None or not config.active:
                continue
            if dry_run:
                print(f"Would deliver {delivery.id} ({delivery.event}) to {config.url}")
                processed += 1
                continue
            try:
                deliver_webhook(db, delivery, config)
                db.commit()
                processed += 1
            except Exception as exc:
                print(f"Error delivering {delivery.id}: {exc}", file=sys.stderr)
                db.rollback()
        return processed
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Process pending webhook deliveries")
    parser.add_argument("command", nargs="?", choices=["deliver"], default="deliver", help="Delivery command to run")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between delivery passes")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be delivered without actually delivering")
    args = parser.parse_args()

    if args.loop:
        print(f"Starting webhook delivery loop (interval: {args.interval}s)")
        try:
            while True:
                count = run_deliveries(dry_run=args.dry_run)
                if count:
                    print(f"Processed {count} deliveries")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nShutting down")
    else:
        count = run_deliveries(dry_run=args.dry_run)
        print(f"Processed {count} deliveries")


if __name__ == "__main__":
    main()
