"""CLI runner for processing pending webhook deliveries."""
from __future__ import annotations

import argparse
import sys
import time

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from .database import build_engine, build_session_factory, default_database_url
from .main import ensure_compatible_schema
from .models import WebhookConfig, WebhookDelivery, utcnow
from .repository import cleanup_webhook_deliveries, get_webhook_config
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


def run_cleanup_deliveries(days: int | None = None, dry_run: bool = False) -> int:
    """Delete webhook deliveries older than the configured retention window."""
    from datetime import timedelta

    engine = build_engine(default_database_url())
    ensure_compatible_schema(engine)
    SessionLocal = build_session_factory(engine)
    db = SessionLocal()
    try:
        retention_days = days
        if retention_days is None:
            from .config import get_settings

            retention_days = get_settings().max_webhook_delivery_age_days
        cutoff = utcnow() - timedelta(days=retention_days)
        if dry_run:
            count = db.scalar(select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.created_at < cutoff))
            return int(count or 0)
        deleted = cleanup_webhook_deliveries(db, older_than_days=retention_days)
        db.commit()
        return deleted
    finally:
        db.close()


def run_rotate_key(old_key: str, new_key: str, dry_run: bool = False, database_url: str | None = None) -> int:
    """Re-encrypt Fernet-protected webhook secrets with a new Fernet key."""
    old_fernet = Fernet(old_key.encode("utf-8"))
    new_fernet = Fernet(new_key.encode("utf-8"))
    engine = build_engine(database_url or default_database_url())
    ensure_compatible_schema(engine)
    SessionLocal = build_session_factory(engine)
    db = SessionLocal()
    try:
        configs = list(db.scalars(select(WebhookConfig).where(WebhookConfig.secret_encrypted == 1)).all())
        if dry_run:
            return len(configs)
        for config in configs:
            plaintext = old_fernet.decrypt(config.secret.encode("utf-8"))
            config.secret = new_fernet.encrypt(plaintext).decode("utf-8")
            config.updated_at = utcnow()
            db.add(config)
        db.commit()
        return len(configs)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_re_encrypt_plaintext(dry_run: bool = False, database_url: str | None = None) -> int:
    """Encrypt plaintext webhook secrets with the configured FLOW_WEBHOOK_ENCRYPTION_KEY."""
    from .security import encrypt_secret, webhook_encryption_enabled

    if not webhook_encryption_enabled():
        raise ValueError("FLOW_WEBHOOK_ENCRYPTION_KEY must be set to re-encrypt plaintext webhook secrets.")
    engine = build_engine(database_url or default_database_url())
    ensure_compatible_schema(engine)
    SessionLocal = build_session_factory(engine)
    db = SessionLocal()
    try:
        configs = list(db.scalars(select(WebhookConfig).where(WebhookConfig.secret_encrypted == 0)).all())
        if dry_run:
            return len(configs)
        for config in configs:
            config.secret = encrypt_secret(config.secret)
            config.secret_encrypted = 1
            config.updated_at = utcnow()
            db.add(config)
        db.commit()
        return len(configs)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Process webhook deliveries")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["deliver", "cleanup-deliveries", "rotate-key", "re-encrypt"],
        default="deliver",
        help="Webhook command to run",
    )
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between delivery passes")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be delivered without actually delivering")
    parser.add_argument("--days", type=int, default=None, help="Delete deliveries older than this many days")
    parser.add_argument("--old-key", default=None, help="Existing Fernet key for rotate-key")
    parser.add_argument("--new-key", default=None, help="Replacement Fernet key for rotate-key")
    args = parser.parse_args()

    if args.command == "cleanup-deliveries":
        count = run_cleanup_deliveries(days=args.days, dry_run=args.dry_run)
        action = "Would delete" if args.dry_run else "Deleted"
        print(f"{action} {count} webhook deliveries")
        return

    if args.command == "rotate-key":
        if not args.old_key or not args.new_key:
            parser.error("rotate-key requires --old-key and --new-key")
        count = run_rotate_key(args.old_key, args.new_key, dry_run=args.dry_run)
        action = "Would rotate" if args.dry_run else "Rotated"
        print(f"{action} {count} webhook secrets")
        return

    if args.command == "re-encrypt":
        count = run_re_encrypt_plaintext(dry_run=args.dry_run)
        action = "Would re-encrypt" if args.dry_run else "Re-encrypted"
        print(f"{action} {count} webhook secrets")
        return

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
