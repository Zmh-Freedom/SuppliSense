#!/usr/bin/env python3
"""
One-time migration: MongoDB users -> PostgreSQL, ChromaDB -> pgvector.

Usage:
    cd backend
    PG_HOST=localhost PG_USER=sra PG_PASSWORD=xxx python scripts/migrate_to_pg.py

Idempotent: safe to run multiple times.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure backend is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.config import settings
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


def migrate_knowledge() -> int:
    """Copy ChromaDB documents to PostgreSQL with re-embedding. Returns doc count."""
    try:
        import chromadb
        chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PATH)
        collection = chroma_client.get_collection("knowledge_base")
        existing = collection.get()
    except Exception:
        print("No ChromaDB data found, skipping knowledge base migration")
        return 0

    if not existing or not existing.get("ids"):
        print("ChromaDB collection is empty, skipping")
        return 0

    doc_ids = existing["ids"]
    documents = existing.get("documents") or [""] * len(doc_ids)
    metadatas = existing.get("metadatas") or [{}] * len(doc_ids)

    print(f"Re-embedding {len(doc_ids)} documents...")

    from app.services.embedding import encode
    from psycopg2.extras import execute_values

    embeddings = encode(documents)

    rows = [
        (doc_id, meta.get("source", "") if meta else "", content, emb, json.dumps(meta, ensure_ascii=False))
        for doc_id, content, emb, meta in zip(doc_ids, documents, embeddings, metadatas)
    ]

    with get_cursor() as (conn, cur):
        execute_values(
            cur,
            """INSERT INTO documents (id, source, content, embedding, metadata)
               VALUES %s
               ON CONFLICT (id) DO NOTHING""",
            rows,
        )

    return len(doc_ids)


def main():
    print("=" * 60)
    print("SuppliSense: MongoDB/ChromaDB -> PostgreSQL Migration")
    print("=" * 60)

    # 1. Ensure PG schema exists
    print("\n[1/3] Initializing PostgreSQL schema...")
    ensure_pg_schema()
    print("  Done.")

    # 2. Migrate users
    print("\n[2/3] Migrating users...")
    migrated, skipped = migrate_users()
    print(f"  Users: {migrated} migrated, {skipped} skipped (already exist)")

    # 3. Migrate knowledge base
    print("\n[3/3] Migrating knowledge base...")
    count = migrate_knowledge()
    print(f"  Documents: {count} migrated")

    # Summary
    with get_cursor() as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM users")
        pg_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM documents")
        pg_docs = cur.fetchone()[0]

    print("\n" + "=" * 60)
    print("Migration complete!")
    print(f"  PG users:     {pg_users}")
    print(f"  PG documents: {pg_docs}")
    print()
    print("Next steps:")
    print("  1. Set USE_PG_USERS=true in .env")
    print("  2. Set USE_PGVECTOR=true in .env")
    print("  3. Restart backend")
    print("=" * 60)


if __name__ == "__main__":
    main()
