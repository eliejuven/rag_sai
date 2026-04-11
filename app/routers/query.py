from fastapi import APIRouter, HTTPException

from app.embeddings.client import embed_texts
from app.search.vector_store import vector_store
from app.models import QueryRequest, QueryResponse, ChunkResult
from app.config import SIMILARITY_TOP_K
from app import storage

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(request: QueryRequest):
    """Query the knowledge base with a question. Returns relevant chunks."""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    if vector_store.size == 0:
        raise HTTPException(
            status_code=400,
            detail="No documents ingested yet. Upload PDFs first.",
        )

    query_vectors = await embed_texts([request.question])
    results = vector_store.search(query_vectors[0], top_k=SIMILARITY_TOP_K)

    chunks = []
    for chunk_index, score in results:
        chunk_data = storage.chunks[chunk_index]
        chunks.append(
            ChunkResult(
                text=chunk_data["text"],
                filename=chunk_data["filename"],
                page_number=chunk_data["page_number"],
                score=round(score, 4),
            )
        )

    return QueryResponse(
        answer="Raw retrieval (no LLM generation yet).",
        chunks=chunks,
    )
