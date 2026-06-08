"""
Persistence Layer

Saves and restores the full RAG state to disk so data survives server restarts:
  - storage.chunks + storage.documents  → data/persist/chunks.json
  - vector_store vectors                → data/persist/vectors.npy
  - vector_store chunk indices          → data/persist/vector_meta.json
  - bm25_index internal state           → data/persist/bm25.json

Call load_state() at server startup and save_state() after any ingestion.
"""

import json
import logging
from pathlib import Path

import numpy as np

from app import storage
from app.search.vector_store import vector_store
from app.search.keyword_search import bm25_index

logger = logging.getLogger(__name__)

PERSIST_DIR = Path(__file__).parent.parent / "data" / "persist"

CHUNKS_PATH      = PERSIST_DIR / "chunks.json"
VECTORS_PATH     = PERSIST_DIR / "vectors.npy"
VECTOR_META_PATH = PERSIST_DIR / "vector_meta.json"
BM25_PATH        = PERSIST_DIR / "bm25.json"


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_state() -> None:
    """Persist the full in-memory RAG state to disk."""
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Chunks + documents
    CHUNKS_PATH.write_text(
        json.dumps(
            {"chunks": storage.chunks, "documents": storage.documents},
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    # 2. Vector store
    if vector_store._vectors is not None:
        np.save(str(VECTORS_PATH), vector_store._vectors)
        VECTOR_META_PATH.write_text(
            json.dumps({"chunk_indices": vector_store._chunk_indices}),
            encoding="utf-8",
        )

    # 3. BM25 index
    BM25_PATH.write_text(
        json.dumps(
            {
                "k1": bm25_index._k1,
                "b": bm25_index._b,
                "chunk_indices": bm25_index._chunk_indices,
                "doc_lengths": bm25_index._doc_lengths,
                "avg_doc_length": bm25_index._avg_doc_length,
                "token_freqs": bm25_index._token_freqs,
                "doc_freq": bm25_index._doc_freq,
                "total_docs": bm25_index._total_docs,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    total_chunks = len(storage.chunks)
    total_vectors = vector_store.size
    logger.info("State saved: %d chunks, %d vectors", total_chunks, total_vectors)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_state() -> bool:
    """
    Restore persisted state into the in-memory stores.

    Returns True if state was loaded, False if no persisted state exists.
    """
    if not CHUNKS_PATH.exists():
        logger.info("No persisted state found — starting fresh.")
        return False

    try:
        # 1. Chunks + documents
        data = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
        storage.chunks.clear()
        storage.chunks.extend(data.get("chunks", []))
        storage.documents.clear()
        storage.documents.update(data.get("documents", {}))

        # 2. Vector store
        if VECTORS_PATH.exists() and VECTOR_META_PATH.exists():
            vectors = np.load(str(VECTORS_PATH))
            meta = json.loads(VECTOR_META_PATH.read_text(encoding="utf-8"))
            vector_store._vectors = vectors
            vector_store._chunk_indices = meta["chunk_indices"]

        # 3. BM25 index
        if BM25_PATH.exists():
            b = json.loads(BM25_PATH.read_text(encoding="utf-8"))
            bm25_index._k1 = b["k1"]
            bm25_index._b = b["b"]
            bm25_index._chunk_indices = b["chunk_indices"]
            bm25_index._doc_lengths = b["doc_lengths"]
            bm25_index._avg_doc_length = b["avg_doc_length"]
            bm25_index._token_freqs = b["token_freqs"]
            bm25_index._doc_freq = b["doc_freq"]
            bm25_index._total_docs = b["total_docs"]

        logger.info(
            "State loaded: %d chunks, %d vectors",
            len(storage.chunks),
            vector_store.size,
        )
        return True

    except Exception as e:
        logger.error("Failed to load persisted state: %s", e)
        return False


# ---------------------------------------------------------------------------
# Convenience check
# ---------------------------------------------------------------------------

def has_persisted_state() -> bool:
    return CHUNKS_PATH.exists()
