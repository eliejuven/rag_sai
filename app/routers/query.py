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
from app.query.company_extractor import extract_company, extract_year
from app.generation.llm import chat_completion
from app.generation.prompts import build_system_prompt, build_rag_prompt, build_market_prompt, RAG_SYSTEM_PROMPT, MARKET_SYSTEM_PROMPT
from app.scraper.market_data import resolve_ticker, fetch_market_data, format_market_context
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

async def _search_and_answer(
    question: str,
    alias_hint: str | None = None,
    requested_year: int | None = None,
) -> QueryResponse:
    """Run the full RAG search pipeline and return a QueryResponse."""
    search_query = await transform_query(question)
    query_vectors = await embed_texts([search_query])

    # Fetch more candidates when a specific year is requested so we don't
    # miss year-specific chunks that rank below the default top-k.
    top_k = SIMILARITY_TOP_K * 3 if requested_year else SIMILARITY_TOP_K

    semantic_results = vector_store.search(query_vectors[0], top_k=top_k)
    keyword_results = bm25_index.search(search_query, top_k=top_k)

    best_semantic_score = semantic_results[0][1] if semantic_results else 0.0
    if best_semantic_score < SIMILARITY_THRESHOLD:
        return None  # caller handles the fallback

    merged = reciprocal_rank_fusion(semantic_results, keyword_results, top_k=top_k)

    all_chunk_pairs = []
    for chunk_index, score in merged:
        all_chunk_pairs.append((chunk_index, score, storage.chunks[chunk_index]))

    # Boost: ensure year-specific chunks are represented in the final context
    if requested_year:
        year_str = str(requested_year)
        search_words = set(search_query.lower().split())
        already_included = {i for i, _, _ in all_chunk_pairs}

        # Scan the full store for year-matching chunks not yet in results
        extra_year_chunks = []
        for idx, chunk in enumerate(storage.chunks):
            if year_str not in chunk.get("text", ""):
                continue
            if idx in already_included:
                continue
            chunk_lower = chunk["text"].lower()
            relevance = sum(1 for w in search_words if len(w) > 3 and w in chunk_lower)
            if relevance > 0:
                extra_year_chunks.append((idx, 0.001, chunk, relevance))

        # Sort extra by relevance and prepend to results
        extra_year_chunks.sort(key=lambda x: x[3], reverse=True)
        extra_triples = [(i, s, c) for i, s, c, _ in extra_year_chunks[:SIMILARITY_TOP_K]]

        # Recombine: year matches from normal search first, then extras, then others
        year_matches = [(i, s, c) for i, s, c in all_chunk_pairs if year_str in c.get("text", "")]
        others = [(i, s, c) for i, s, c in all_chunk_pairs if year_str not in c.get("text", "")]
        all_chunk_pairs = year_matches + extra_triples + others

    all_chunk_pairs = all_chunk_pairs[:SIMILARITY_TOP_K]

    chunks = []
    chunk_dicts = []
    for chunk_index, score, chunk_data in all_chunk_pairs:
        chunks.append(
            ChunkResult(
                text=chunk_data["text"],
                filename=chunk_data["filename"],
                page_number=chunk_data["page_number"],
                score=round(score, 4),
            )
        )
        chunk_dicts.append(chunk_data)

    user_message = build_rag_prompt(question, chunk_dicts, alias_hint=alias_hint)
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

    if intent == "market":
        company_name = await extract_company(question)
        if company_name:
            ticker = resolve_ticker(company_name)
            if ticker:
                try:
                    data = await asyncio.to_thread(fetch_market_data, ticker)
                    snap = data["snapshot"]
                    has_data = any(snap.get(k) is not None for k in ("price", "market_cap", "pe_ratio"))
                    alias_hint = None
                    if company_name.lower() not in question.lower():
                        alias_hint = (
                            f"The user is asking about '{company_name}'. "
                            f"Treat any alternative names as referring to the same company."
                        )
                    market_text = format_market_context(data, company_name, ticker)
                    user_message = build_market_prompt(question, market_text, alias_hint=alias_hint)
                    answer = await chat_completion(MARKET_SYSTEM_PROMPT, user_message, temperature=0.2)
                    return QueryResponse(answer=answer, grounded=has_data, chunks=[])
                except Exception:
                    pass
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

            if intent == "market":
                await emit("Identificando empresa na pergunta...")
                company_name = await extract_company(question)
                if not company_name:
                    await emit("Nenhuma empresa detectada. Respondendo com conhecimento geral.")
                    answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
                    await queue.put({"type": "answer", "answer": answer, "grounded": False, "chunks": []})
                    return

                await emit(f"Empresa detectada: {company_name}")
                alias_hint = None
                if company_name.lower() not in question.lower():
                    alias_hint = (
                        f"The user is asking about '{company_name}'. "
                        f"Treat any alternative names as referring to the same company."
                    )

                ticker = resolve_ticker(company_name)
                if ticker is None:
                    await emit(
                        f"Ticker de {company_name} não encontrado na lista de empresas suportadas. "
                        "Respondendo com conhecimento geral."
                    )
                    answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
                    await queue.put({"type": "answer", "answer": answer, "grounded": False, "chunks": []})
                    return

                await emit(f"Buscando dados de {company_name} ({ticker}) no Yahoo Finance...")
                try:
                    data = await asyncio.to_thread(fetch_market_data, ticker)
                except Exception as e:
                    await emit(f"Erro ao buscar dados de mercado: {e}. Respondendo com conhecimento geral.")
                    answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
                    await queue.put({"type": "answer", "answer": answer, "grounded": False, "chunks": []})
                    return

                await emit("Gerando resposta...")
                market_text = format_market_context(data, company_name, ticker)
                snap = data["snapshot"]
                has_data = any(snap.get(k) is not None for k in ("price", "market_cap", "pe_ratio"))
                user_message = build_market_prompt(question, market_text, alias_hint=alias_hint)
                answer = await chat_completion(MARKET_SYSTEM_PROMPT, user_message, temperature=0.2)
                await queue.put({"type": "answer", "answer": answer, "grounded": has_data, "chunks": []})
                return

            # ---- 2. Extract company and year from question ----
            await emit("Identificando empresa na pergunta...")
            company_name = await extract_company(question)
            requested_year = extract_year(question)
            alias_hint = None
            if company_name:
                await emit(f"Empresa detectada: {company_name}")
                # Build alias hint if the official name differs from what user typed
                if company_name.lower() not in question.lower():
                    alias_hint = (
                        f"The user is asking about '{company_name}'. "
                        f"In the retrieved documents this company may appear under its "
                        f"official registered name (e.g. 'TELEFÔNICA BRASIL' for 'Vivo', "
                        f"'PETRÓLEO BRASILEIRO' for 'Petrobras'). "
                        f"Treat all such names as referring to the same company."
                    )
            else:
                await emit("Nenhuma empresa específica detectada.")

            # ---- 3. First search attempt ----
            await emit("Buscando na base de conhecimento...")
            result = None
            if vector_store.size > 0:
                result = await _search_and_answer(question, alias_hint=alias_hint, requested_year=requested_year)

            # Discard results if they belong to a different company
            if result is not None and company_name:
                norm = company_name.lower()
                chunks_match = any(
                    norm.split()[0] in c.filename.lower()
                    for c in result.chunks
                )
                if not chunks_match:
                    await emit(
                        f"Resultados encontrados não são sobre {company_name}. "
                        "Buscando dados específicos..."
                    )
                    result = None

            # Discard results if they don't cover the requested year
            if result is not None and requested_year:
                year_str = str(requested_year)
                year_covered = any(
                    year_str in c.text
                    for c in result.chunks
                )
                if not year_covered:
                    await emit(
                        f"Dados encontrados não cobrem {requested_year}. "
                        "Buscando ano específico na CVM..."
                    )
                    result = None

            # ---- 4. If no relevant data found → try scraping ----
            if result is None:
                if company_name:
                    scraped = await scrape_and_ingest(
                        company_name, progress=emit, requested_year=requested_year
                    )

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
                    result = await _search_and_answer(question, alias_hint=alias_hint, requested_year=requested_year)

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
