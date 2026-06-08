import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.embeddings.client import embed_texts
from app.search.vector_store import vector_store
from app.search.keyword_search import bm25_index
from app.search.reranker import reciprocal_rank_fusion
from app.query.intent import detect_intent
from app.query.transform import transform_query
from app.query.company_extractor import extract_company
from app.generation.llm import chat_completion
from app.generation.prompts import build_system_prompt, build_rag_prompt
from app.scraper.pipeline import scrape_and_ingest
from app.models import QueryRequest, QueryResponse, ChunkResult
from app.config import SIMILARITY_TOP_K, SIMILARITY_THRESHOLD
from app import storage

GENERAL_SYSTEM_PROMPT = "You are a friendly assistant. Answer the user naturally and concisely."

router = APIRouter()


def _general_response(answer: str) -> QueryResponse:
    return QueryResponse(answer=answer, grounded=False, chunks=[])


# ---------------------------------------------------------------------------
# Shared search logic (used by both endpoints)
# ---------------------------------------------------------------------------

async def _search_and_answer(question: str) -> QueryResponse:
    """Run the full RAG search pipeline and return a QueryResponse."""
    search_query = await transform_query(question)
    query_vectors = await embed_texts([search_query])
    semantic_results = vector_store.search(query_vectors[0], top_k=SIMILARITY_TOP_K)
    keyword_results = bm25_index.search(search_query, top_k=SIMILARITY_TOP_K)

    best_semantic_score = semantic_results[0][1] if semantic_results else 0.0
    if best_semantic_score < SIMILARITY_THRESHOLD:
        return None  # caller handles the fallback

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
    system_prompt = build_system_prompt(chunk_dicts)
    answer = await chat_completion(system_prompt, user_message, temperature=0.2)

    return QueryResponse(answer=answer, grounded=True, chunks=chunks)


# ---------------------------------------------------------------------------
# Existing endpoint (unchanged behaviour)
# ---------------------------------------------------------------------------

@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(request: QueryRequest):
    """Query the knowledge base. Returns relevant chunks."""
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    intent = await detect_intent(question)
    if intent == "chat":
        answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
        return _general_response(answer)

    if vector_store.size == 0:
        answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
        return _general_response(answer)

    result = await _search_and_answer(question)
    if result is None:
        answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
        return _general_response(answer)

    return result


# ---------------------------------------------------------------------------
# SSE streaming endpoint with auto-scrape
# ---------------------------------------------------------------------------

@router.post("/query/stream")
async def query_stream(request: QueryRequest):
    """
    SSE endpoint. Streams progress messages then the final answer.

    Event format:
      data: {"type": "progress", "message": "..."}
      data: {"type": "answer",   "answer": "...", "grounded": bool, "chunks": [...]}
      data: {"type": "error",    "message": "..."}
    """
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    queue: asyncio.Queue = asyncio.Queue()

    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def emit(message: str) -> None:
        await queue.put({"type": "progress", "message": message})

    async def run_pipeline() -> None:
        try:
            # ---- 1. Intent detection ----
            await emit("Analisando sua pergunta...")
            intent = await detect_intent(question)

            if intent == "chat":
                answer = await chat_completion(
                    GENERAL_SYSTEM_PROMPT, question, temperature=0.5
                )
                await queue.put({
                    "type": "answer",
                    "answer": answer,
                    "grounded": False,
                    "chunks": [],
                })
                return

            # ---- 2. First search attempt ----
            await emit("Buscando na base de conhecimento...")
            result = None
            if vector_store.size > 0:
                result = await _search_and_answer(question)

            # ---- 3. If no relevant data found → try scraping ----
            if result is None:
                await emit("Dados insuficientes na base. Identificando empresa...")
                company_name = await extract_company(question)

                if company_name:
                    scraped = await scrape_and_ingest(company_name, progress=emit)

                    if scraped.get("error"):
                        # Scraping failed — fall back to general LLM but inform user
                        await emit(
                            "Não foi possível obter dados financeiros. "
                            "Respondendo com conhecimento geral."
                        )
                        answer = await chat_completion(
                            GENERAL_SYSTEM_PROMPT, question, temperature=0.5
                        )
                        await queue.put({
                            "type": "answer",
                            "answer": answer,
                            "grounded": False,
                            "chunks": [],
                        })
                        return

                    # Re-search now that data is indexed
                    await emit("Buscando na base atualizada...")
                    result = await _search_and_answer(question)

                if result is None:
                    # Still nothing — general fallback
                    await emit("Respondendo com conhecimento geral.")
                    answer = await chat_completion(
                        GENERAL_SYSTEM_PROMPT, question, temperature=0.5
                    )
                    await queue.put({
                        "type": "answer",
                        "answer": answer,
                        "grounded": False,
                        "chunks": [],
                    })
                    return

            # ---- 4. Stream the grounded answer ----
            await emit("Gerando resposta...")
            await queue.put({
                "type": "answer",
                "answer": result.answer,
                "grounded": result.grounded,
                "chunks": [c.model_dump() for c in result.chunks],
            })

        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})
        finally:
            await queue.put(None)  # sentinel — stream is done

    async def event_generator():
        asyncio.create_task(run_pipeline())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield _sse(item)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables nginx buffering
        },
    )
