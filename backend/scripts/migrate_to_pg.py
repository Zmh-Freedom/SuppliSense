#!/usr/bin/env python3
"""
One-time migration: MongoDB users -> PostgreSQL.

Usage:
    cd backend
    PG_HOST=localhost PG_USER=sra PG_PASSWORD=xxx python scripts/migrate_to_pg.py

Idempotent: safe to run multiple times.
"""

import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure backend is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.db.init_pg import ensure_pg_schema
from app.db.mongo import get_db
from app.db.postgres import get_cursor


def migrate_users() -> tuple[int, int]:
    """Copy users from MongoDB to PostgreSQL. Returns (migrated, skipped)."""
    db = get_db()
    mongo_users = list(db["users"].find())

    # Get existing PG usernames
    with get_cursor() as (conn, cur):
        cur.execute("SELECT username FROM users")
        existing = {row[0] for row in cur.fetchall()}

    migrated = 0
    skipped = 0

    for mu in mongo_users:
        username = mu.get("username", "")
        if username in existing:
            skipped += 1
            continue

        user_id = str(uuid.uuid4())
        email = mu.get("email", "")
        password_hash = mu.get("password_hash", "")
        role = mu.get("role", "viewer")

        with get_cursor() as (conn, cur):
            cur.execute(
                """INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (username) DO NOTHING""",
                (user_id, username, email, password_hash, role, True,
                 mu.get("created_at", datetime.now(timezone.utc)),
                 datetime.now(timezone.utc)),
            )
        migrated += 1

    return migrated, skipped


def main():
    print("=" * 60)
    print("SuppliSense: MongoDB -> PostgreSQL User Migration")
    print("=" * 60)

    # 1. Ensure PG schema exists
    print("\n[1/3] Initializing PostgreSQL schema...")
    ensure_pg_schema()
    print("  Done.")

    # 2. Migrate users
    print("\n[2/3] Migrating users...")
    migrated, skipped = migrate_users()
    print(f"  Users: {migrated} migrated, {skipped} skipped (already exist)")

    # Summary
    with get_cursor() as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM users")
        pg_users = cur.fetchone()[0]

    print("\n" + "=" * 60)
    print("Migration complete!")
    print(f"  PG users:     {pg_users}")
    print()
    print("Next steps:")
    print("  1. Set USE_PG_USERS=true in .env")
    print("  2. Restart backend")
    print("=" * 60)


if __name__ == "__main__":
    main()
