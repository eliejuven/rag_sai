from fastapi import APIRouter, HTTPException

from app.embeddings.client import embed_texts
from app.search.vector_store import vector_store
from app.search.keyword_search import bm25_index
from app.search.reranker import reciprocal_rank_fusion
from app.query.intent import detect_intent
from app.query.transform import transform_query
from app.generation.llm import chat_completion
from app.generation.prompts import RAG_SYSTEM_PROMPT, build_rag_prompt
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
    semantic_results = vector_store.search(query_vectors[0], top_k=SIMILARITY_TOP_K)
    keyword_results = bm25_index.search(search_query, top_k=SIMILARITY_TOP_K)

    merged = reciprocal_rank_fusion(
        semantic_results, keyword_results, top_k=SIMILARITY_TOP_K
    )

    chunks = []
    chunk_dicts = []
    for chunk_index, score in merged:
        chunk_data = storage.chunks[chunk_index]
        chunks.append(
            ChunkResult(
                text=chunk_data["text"],
                filename=chunk_data["filename"],
                page_number=chunk_data["page_number"],
                score=round(score, 4),
            )
        )
        chunk_dicts.append(chunk_data)

    user_message = build_rag_prompt(question, chunk_dicts)
    answer = await chat_completion(RAG_SYSTEM_PROMPT, user_message, temperature=0.2)

    return QueryResponse(answer=answer, chunks=chunks)
