"""`flow-backup` — backup and restore the Flow SQLite database."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings, reset_settings_cache
from .database import build_engine, default_database_url


def _db_path() -> Path:
    """Return the filesystem path of the SQLite database."""
    url = default_database_url()
    # sqlite:///path/to/file.db → path/to/file.db
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return Path(url[len(prefix):])
    raise RuntimeError(f"Backup only supports SQLite databases, got: {url}")


def _lock_marker_path() -> Path:
    """Return the path of the file whose presence indicates a running server."""
    return _db_path().parent / ".flow-server-lock"


def _default_backup_dir() -> Path:
    settings = get_settings()
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def cmd_backup(output_dir: Path | None = None) -> int:
    """Create a consistent snapshot of the SQLite database using VACUUM INTO."""
    db_path = _db_path()
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        return 1

    backup_dir = output_dir or _default_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"flow-backup-{timestamp}.db"

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(f"VACUUM INTO '{backup_path}'")
        finally:
            conn.close()
    except Exception as exc:
        print(f"Error: backup failed: {exc}", file=sys.stderr)
        return 1

    print(backup_path)
    return 0


def cmd_restore(backup_path: Path, dry_run: bool = False) -> int:
    """Restore the database from a backup file."""
    if not backup_path.exists():
        print(f"Error: backup file not found: {backup_path}", file=sys.stderr)
        return 1

    # Validate the backup is a working SQLite database
    try:
        conn = sqlite3.connect(str(backup_path))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                print(f"Error: backup file failed integrity check: {result[0]}", file=sys.stderr)
                return 1
        finally:
            conn.close()
    except Exception as exc:
        print(f"Error: backup file is not a valid SQLite database: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        print(f"Dry run: would restore from {backup_path} to {_db_path()}")
        print("Integrity check: ok")
        return 0

    # Check if server appears to be running
    lock_path = _lock_marker_path()
    if lock_path.exists():
        print("Error: server appears to be running (lock file present). Stop flow-serve before restoring.", file=sys.stderr)
        return 1

    db_path = _db_path()
    try:
        shutil.copy2(backup_path, db_path)
    except Exception as exc:
        print(f"Error: restore failed: {exc}", file=sys.stderr)
        return 1

    print(f"Restored database from {backup_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="flow-backup", description="Backup and restore the Flow SQLite database.")
    sub = parser.add_subparsers(dest="command")

    backup_parser = sub.add_parser("backup", help="Create a database backup.")
    backup_parser.add_argument("--output-dir", type=Path, default=None, help="Directory to write the backup file.")

    restore_parser = sub.add_parser("restore", help="Restore database from a backup file.")
    restore_parser.add_argument("path", type=Path, help="Path to the backup file.")
    restore_parser.add_argument("--dry-run", action="store_true", help="Validate without overwriting.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "backup":
        return cmd_backup(output_dir=args.output_dir)
    elif args.command == "restore":
        return cmd_restore(backup_path=args.path, dry_run=args.dry_run)
    else:
        _parse_args(["--help"])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())