from fastapi import APIRouter, HTTPException

from app.embeddings.client import embed_texts
from app.search.vector_store import vector_store
from app.query.intent import detect_intent
from app.query.transform import transform_query
from app.models import QueryRequest, QueryResponse, ChunkResult
from app.config import SIMILARITY_TOP_K
from app import storage

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(request: QueryRequest):
    """Query the knowledge base with a question. Returns relevant chunks."""
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    intent = await detect_intent(question)

    if intent == "chat":
        return QueryResponse(
            answer="Hello! I'm a document Q&A assistant. Upload some PDFs and ask me questions about them.",
            chunks=[],
        )

    if vector_store.size == 0:
        raise HTTPException(
            status_code=400,
            detail="No documents ingested yet. Upload PDFs first.",
        )

    search_query = await transform_query(question)

    query_vectors = await embed_texts([search_query])
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
