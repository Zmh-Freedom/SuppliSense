"""
Knowledge Base: Vector database for RAG.
Uses PostgreSQL + pgvector when USE_PGVECTOR=true, ChromaDB otherwise.
"""

import json
import os
from typing import Any

from app.core.config import settings

COLLECTION_NAME = "knowledge_base"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def add_documents(
    documents: list[str],
    metadatas: list[dict[str, Any]],
    ids: list[str],
) -> None:
    if settings.USE_PGVECTOR:
        _add_documents_pg(documents, metadatas, ids)
    else:
        _add_documents_chroma(documents, metadatas, ids)


def search(
    query: str,
    n_results: int = 5,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if settings.USE_PGVECTOR:
        return _search_pg(query, n_results, filters)
    return _search_chroma(query, n_results, filters)


def delete_documents(ids: list[str]) -> None:
    if settings.USE_PGVECTOR:
        _delete_documents_pg(ids)
    else:
        _delete_documents_chroma(ids)


def get_stats() -> dict[str, Any]:
    if settings.USE_PGVECTOR:
        return _get_stats_pg()
    return _get_stats_chroma()


def clear_all() -> None:
    if settings.USE_PGVECTOR:
        _clear_all_pg()
    else:
        _clear_all_chroma()


# ---- pgvector implementations ----

def _add_documents_pg(
    documents: list[str],
    metadatas: list[dict[str, Any]],
    ids: list[str],
) -> None:
    from app.services.embedding import encode
    from app.db.postgres import get_cursor
    from psycopg2.extras import execute_values

    embeddings = encode(documents)
    rows = [
        (doc_id, meta.get("source", ""), content, emb, json.dumps(meta, ensure_ascii=False))
        for doc_id, content, emb, meta in zip(ids, documents, embeddings, metadatas)
    ]
    with get_cursor() as (conn, cur):
        execute_values(
            cur,
            """INSERT INTO documents (id, source, content, embedding, metadata)
               VALUES %s
               ON CONFLICT (id) DO UPDATE
               SET content = EXCLUDED.content,
                   embedding = EXCLUDED.embedding,
                   metadata = EXCLUDED.metadata""",
            rows,
        )


def _search_pg(
    query: str,
    n_results: int = 5,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from app.services.embedding import encode_single
    from app.db.postgres import get_cursor

    query_embedding = encode_single(query)

    with get_cursor() as (conn, cur):
        where_clauses: list[str] = []
        where_params: list = []

        if filters:
            for key, value in filters.items():
                if isinstance(value, dict) and "$contains" in value:
                    where_clauses.append("metadata->>%s ILIKE %s")
                    where_params.append(key)
                    where_params.append(f"%{value['$contains']}%")
                else:
                    where_clauses.append("metadata->>%s = %s")
                    where_params.append(key)
                    where_params.append(str(value))

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        cur.execute(
            f"""SELECT id, source, content, metadata,
                       1 - (embedding <=> %s::vector) AS similarity
               FROM documents
               WHERE {where_sql}
               ORDER BY similarity DESC
               LIMIT %s""",
            [query_embedding] + where_params + [n_results],
        )

        formatted = []
        for row in cur.fetchall():
            meta = row[3] if isinstance(row[3], dict) else {}
            formatted.append({
                "content": row[2],
                "metadata": meta,
                "distance": round(1.0 - float(row[4]), 6),
                "id": row[0],
                "source": row[1],
            })

        return formatted


def _delete_documents_pg(ids: list[str]) -> None:
    from app.db.postgres import get_cursor

    with get_cursor() as (conn, cur):
        cur.execute("DELETE FROM documents WHERE id = ANY(%s)", (ids,))


def _get_stats_pg() -> dict[str, Any]:
    from app.db.postgres import get_cursor

    with get_cursor() as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM documents")
        count = cur.fetchone()[0]
    return {
        "total_documents": count,
        "collection_name": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
    }


def _clear_all_pg() -> None:
    from app.db.postgres import get_cursor

    with get_cursor() as (conn, cur):
        cur.execute("DELETE FROM documents")


# ---- ChromaDB implementations (legacy) ----

CHROMA_PATH = os.getenv("CHROMA_PATH", "./data/chroma")
_client: Any = None
_collection: Any = None


def _get_client_chroma():
    global _client
    if _client is None:
        import chromadb
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def _get_collection_chroma():
    global _collection
    if _collection is None:
        client = _get_client_chroma()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _add_documents_chroma(documents, metadatas, ids) -> None:
    collection = _get_collection_chroma()
    collection.add(documents=documents, metadatas=metadatas, ids=ids)


def _search_chroma(query, n_results, filters) -> list[dict[str, Any]]:
    collection = _get_collection_chroma()
    query_params = {"query_texts": [query], "n_results": n_results}
    if filters:
        query_params["where"] = filters

    results = collection.query(**query_params)

    formatted = []
    if results and results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            formatted.append({
                "content": doc,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else None,
                "id": results["ids"][0][i] if results["ids"] else None,
            })
    return formatted


def _delete_documents_chroma(ids) -> None:
    collection = _get_collection_chroma()
    collection.delete(ids=ids)


def _get_stats_chroma() -> dict[str, Any]:
    collection = _get_collection_chroma()
    return {
        "total_documents": collection.count(),
        "collection_name": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
    }


def _clear_all_chroma() -> None:
    global _collection
    client = _get_client_chroma()
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    _collection = None
