"""
Embedding generation using sentence-transformers.
Replaces ChromaDB's built-in embedding function.
"""

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def encode(texts: list[str]) -> list[list[float]]:
    embeddings = get_model().encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def encode_single(text: str) -> list[float]:
    return encode([text])[0]


def warmup() -> None:
    """预加载 embedding 模型，避免首次调用时长时间阻塞。"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info("warming up embedding model")
    get_model()
    # Run a test encoding to fully initialize
    encode_single("test")
    logger.info("embedding model ready")
