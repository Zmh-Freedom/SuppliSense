"""
Knowledge Base: Vector database for RAG (Retrieval Augmented Generation)
Uses ChromaDB for vector storage and sentence-transformers for embeddings.
"""

import os
from typing import Any

import chromadb
from chromadb.config import Settings


# ChromaDB collection name
COLLECTION_NAME = "knowledge_base"

# Embedding model
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# ChromaDB storage path
CHROMA_PATH = os.getenv("CHROMA_PATH", "./data/chroma")

# Global client and collection
_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None


def get_client() -> chromadb.ClientAPI:
    """Get or create ChromaDB client."""
    global _client
    if _client is None:
        # Ensure storage directory exists
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def get_collection() -> chromadb.Collection:
    """Get or create the knowledge base collection."""
    global _collection
    if _collection is None:
        client = get_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},  # Use cosine similarity
        )
    return _collection


def add_documents(
    documents: list[str],
    metadatas: list[dict[str, Any]],
    ids: list[str],
) -> None:
    """
    Add documents to the knowledge base.

    Args:
        documents: List of document text chunks
        metadatas: List of metadata dicts for each document
        ids: List of unique IDs for each document
    """
    collection = get_collection()

    # ChromaDB will automatically generate embeddings using the default embedding function
    collection.add(
        documents=documents,
        metadatas=metadatas,
        ids=ids,
    )


def search(
    query: str,
    n_results: int = 5,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Search the knowledge base for relevant documents.

    Args:
        query: Search query text
        n_results: Number of results to return
        filters: Optional metadata filters (e.g., {"source": "report.pdf"})

    Returns:
        List of dicts with keys: content, metadata, distance
    """
    collection = get_collection()

    # Build query parameters
    query_params = {
        "query_texts": [query],
        "n_results": n_results,
    }

    if filters:
        query_params["where"] = filters

    results = collection.query(**query_params)

    # Format results
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


def delete_documents(ids: list[str]) -> None:
    """Delete documents by IDs."""
    collection = get_collection()
    collection.delete(ids=ids)


def get_stats() -> dict[str, Any]:
    """Get knowledge base statistics."""
    collection = get_collection()
    count = collection.count()
    return {
        "total_documents": count,
        "collection_name": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
    }


def clear_all() -> None:
    """Clear all documents from the knowledge base."""
    global _collection
    client = get_client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    _collection = None
