"""
Scraping Pipeline

Orchestrates the full flow for auto-fetching a company's financial statements:
  1. Resolve company name → CVM registry (CNPJ)
  2. Check staleness (skip if data is fresh)
  3. Fetch DFP + ITR statements from CVM
  4. Chunk → embed → store in the existing vector store + BM25 index
  5. Persist metadata (last scraped timestamp, chunk indices)

Progress is reported via an async callback so the SSE endpoint can stream
live updates to the user.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from app.scraper.cvm_registry import lookup_company
from app.scraper.cvm_client import fetch_statements
from app.ingestion.chunker import chunk_pages
from app.embeddings.client import embed_texts
from app.search.vector_store import vector_store
from app.search.keyword_search import bm25_index
from app import storage
from app.persistence import save_state

logger = logging.getLogger(__name__)

METADATA_PATH = Path(__file__).parent.parent.parent / "data" / "scraped_companies.json"
STALENESS_DAYS = 90

ProgressCallback = Callable[[str], Awaitable[None]]


async def _noop(_: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Metadata helpers (track what has been scraped and when)
# ---------------------------------------------------------------------------

def _load_metadata() -> dict:
    if METADATA_PATH.exists():
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    return {}


def _save_metadata(metadata: dict) -> None:
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _is_stale(metadata: dict, cnpj: str, requested_year: int | None = None) -> bool:
    """Return True if the company has never been scraped, data is old, or a
    requested year is missing from the already-indexed data."""
    if cnpj not in metadata:
        return True
    last_scraped_str = metadata[cnpj].get("last_scraped")
    if not last_scraped_str:
        return True

    # If the user asked about a specific year not yet indexed, force re-scrape
    if requested_year:
        indexed_years = metadata[cnpj].get("dfp_years", [])
        if requested_year not in indexed_years:
            return True

    last_scraped = datetime.fromisoformat(last_scraped_str)
    return datetime.now() - last_scraped > timedelta(days=STALENESS_DAYS)


# ---------------------------------------------------------------------------
# Public pipeline
# ---------------------------------------------------------------------------

async def scrape_and_ingest(
    company_query: str,
    progress: ProgressCallback | None = None,
    requested_year: int | None = None,
) -> dict:
    """
    Full pipeline: resolve company → check staleness → scrape → ingest.

    Args:
        company_query: Free-text company name extracted from the user's question
                       (e.g. "Petrobras", "Banco do Brasil").
        progress:      Async callback called with a human-readable status message
                       at each step. Used by the SSE endpoint to stream progress.

    Returns:
        {
            "company":       dict with cnpj, name, trade_name, cd_cvm
            "scraped":       bool — False if fresh cached data was reused
            "pages_fetched": int
            "chunks_added":  int
            "error":         str | None
        }
    """
    emit = progress or _noop

    # ------------------------------------------------------------------
    # Step 1 — Resolve company name via CVM registry
    # ------------------------------------------------------------------
    await emit(f"Procurando '{company_query}' no cadastro CVM...")

    company = lookup_company(company_query)
    if company is None:
        msg = (
            f"Empresa '{company_query}' não encontrada no cadastro CVM. "
            "Verifique o nome ou tente com o nome oficial (ex: 'Telefônica Brasil' em vez de 'Vivo')."
        )
        await emit(f"ERRO: {msg}")
        return {"company": None, "scraped": False, "pages_fetched": 0, "chunks_added": 0, "error": msg}

    display_name = company["trade_name"] or company["name"]
    await emit(f"Empresa identificada: {display_name} (CNPJ: {company['cnpj']})")

    # ------------------------------------------------------------------
    # Step 2 — Staleness check
    # ------------------------------------------------------------------
    metadata = _load_metadata()
    cnpj = company["cnpj"]

    if not _is_stale(metadata, cnpj, requested_year=requested_year):
        last = metadata[cnpj]["last_scraped"][:10]
        await emit(
            f"Dados de {display_name} já estão indexados e atualizados "
            f"(última coleta: {last}). Usando dados existentes."
        )
        return {
            "company": company,
            "scraped": False,
            "pages_fetched": metadata[cnpj].get("pages_fetched", 0),
            "chunks_added": metadata[cnpj].get("chunks_added", 0),
            "error": None,
        }

    # ------------------------------------------------------------------
    # Step 3 — Fetch financial statements from CVM
    # ------------------------------------------------------------------
    await emit(f"Baixando demonstrações financeiras de {display_name} na CVM...")
    await emit("  → DFP (balanços anuais)...")

    # Build year lists — always include requested_year if provided and not in default range
    from datetime import date as _date
    current_year = _date.today().year
    dfp_years = [current_year - 1, current_year - 2]
    itr_years = [current_year, current_year - 1]
    if requested_year and requested_year not in dfp_years:
        dfp_years.append(requested_year)
        dfp_years = sorted(set(dfp_years), reverse=True)

    try:
        pages = fetch_statements(
            cnpj=cnpj,
            company_name=display_name,
            dfp_years=dfp_years,
            itr_years=itr_years,
        )
    except Exception as e:
        msg = f"Falha ao baixar dados da CVM para {display_name}: {e}"
        logger.error(msg)
        await emit(f"ERRO: {msg}")
        return {"company": company, "scraped": False, "pages_fetched": 0, "chunks_added": 0, "error": msg}

    if not pages:
        msg = f"Nenhum dado encontrado na CVM para {display_name} (CNPJ: {cnpj})."
        await emit(f"AVISO: {msg}")
        return {"company": company, "scraped": False, "pages_fetched": 0, "chunks_added": 0, "error": msg}

    await emit(f"  → {len(pages)} demonstrações baixadas.")

    # ------------------------------------------------------------------
    # Step 4 — Chunk
    # ------------------------------------------------------------------
    await emit("Dividindo em chunks para indexação...")

    doc_chunks = chunk_pages(pages)
    for chunk in doc_chunks:
        chunk["document_id"] = f"cvm_{cnpj}"
        chunk["filename"] = f"{display_name} (CVM)"
        chunk["source"] = "CVM"
        chunk["cnpj"] = cnpj

    # ------------------------------------------------------------------
    # Step 5 — Embed + store
    # ------------------------------------------------------------------
    await emit(f"Indexando {len(doc_chunks)} chunks no vector store...")

    start_index = len(storage.chunks)
    storage.chunks.extend(doc_chunks)

    chunk_texts = [c["text"] for c in doc_chunks]
    chunk_indices = list(range(start_index, start_index + len(doc_chunks)))

    vectors = await embed_texts(chunk_texts)
    vector_store.add(vectors, chunk_indices)
    bm25_index.add(chunk_texts, chunk_indices)

    # Also register the document
    storage.documents[f"cvm_{cnpj}"] = {
        "filename": f"{display_name} (CVM)",
        "pages": pages,
        "source": "CVM",
        "cnpj": cnpj,
    }

    # ------------------------------------------------------------------
    # Step 6 — Persist metadata
    # ------------------------------------------------------------------
    metadata[cnpj] = {
        "company_name": display_name,
        "cnpj": cnpj,
        "cd_cvm": company.get("cd_cvm", ""),
        "last_scraped": datetime.now().isoformat(),
        "pages_fetched": len(pages),
        "chunks_added": len(doc_chunks),
        "chunk_indices": chunk_indices,
        "dfp_years": dfp_years,
        "itr_years": itr_years,
    }
    _save_metadata(metadata)
    save_state()

    await emit(
        f"Concluído! {display_name}: {len(pages)} demonstrações, "
        f"{len(doc_chunks)} chunks indexados."
    )

    return {
        "company": company,
        "scraped": True,
        "pages_fetched": len(pages),
        "chunks_added": len(doc_chunks),
        "error": None,
    }
