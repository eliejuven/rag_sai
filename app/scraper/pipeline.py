"""
Scraping Pipeline

Orchestrates the full flow for auto-fetching a company's financial data:
  1. Resolve company name → CVM registry (CNPJ)
  2. Check staleness (skip if data is fresh and FRE year is indexed)
  3a. Fetch DFP + ITR structured statements from CVM (numbers)
  3b. Fetch Formulário de Referência sections from CVM (qualitative)
  4. Chunk → embed → store in the existing vector store + BM25 index
  5. Persist metadata (last scraped timestamp, indexed years)

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
from app.scraper.fre_client import fetch_fre_sections
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


def _is_stale(
    metadata: dict,
    cnpj: str,
    requested_year: int | None = None,
    fre_year: int | None = None,
) -> bool:
    """
    Return True if the company needs to be re-scraped.

    Triggers a re-scrape when:
    - Company has never been scraped
    - A specific DFP year the user asked about is not yet indexed
    - The FRE for the target year is not yet indexed
    - Last scrape was more than STALENESS_DAYS (90) days ago
    """
    if cnpj not in metadata:
        return True
    last_scraped_str = metadata[cnpj].get("last_scraped")
    if not last_scraped_str:
        return True

    # Re-scrape if a specific DFP year the user asked about is missing
    if requested_year:
        indexed_years = metadata[cnpj].get("dfp_years", [])
        if requested_year not in indexed_years:
            return True

    # Re-scrape if the FRE for the target year has not been indexed yet
    if fre_year:
        indexed_fre_years = metadata[cnpj].get("fre_years", [])
        if fre_year not in indexed_fre_years:
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

    # FRE year: the most recent complete annual filing
    # (current_year - 1, since the current year's FRE may not be filed yet)
    from datetime import date as _date
    fre_year = _date.today().year - 1

    if not _is_stale(metadata, cnpj, requested_year=requested_year, fre_year=fre_year):
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
    # Step 3b — Fetch FRE qualitative sections
    # This runs alongside DFP/ITR. Failure is non-fatal: if the FRE
    # cannot be downloaded (network error, company hasn't filed yet),
    # we log a warning and continue with just the structured data.
    # ------------------------------------------------------------------
    await emit(f"  → Formulário de Referência {fre_year} (seções qualitativas)...")

    fre_pages = []
    try:
        fre_pages, fre_skip_reason = fetch_fre_sections(
            cnpj=cnpj,
            company_name=display_name,
            year=fre_year,
        )
        if fre_pages:
            await emit(f"  → {len(fre_pages)} seções do FRE extraídas.")
        else:
            await emit(f"  → AVISO: FRE {fre_year} não disponível para {display_name}. {fre_skip_reason}")
    except Exception as e:
        logger.warning("FRE scraping failed for %s (non-fatal): %s", display_name, e)
        await emit(f"  → AVISO: FRE indisponível para {display_name} ({e}). Continuando com dados estruturados.")

    # ------------------------------------------------------------------
    # Step 4 — Chunk
    # DFP/ITR pages are chunked together (they form a continuous financial
    # dataset). FRE pages are chunked section by section so each chunk
    # keeps its section metadata (section number, label) for citations.
    # ------------------------------------------------------------------
    await emit("Dividindo em chunks para indexação...")

    # DFP/ITR chunks — merged chunking (existing behaviour)
    doc_chunks = chunk_pages(pages)
    for chunk in doc_chunks:
        chunk["document_id"] = f"cvm_{cnpj}"
        chunk["filename"] = f"{display_name} (CVM)"
        chunk["source"] = "CVM"
        chunk["cnpj"] = cnpj

    # FRE chunks — one section at a time to preserve section metadata
    fre_chunks = []
    for fre_page in fre_pages:
        section_chunks = chunk_pages([fre_page])
        for chunk in section_chunks:
            chunk["document_id"] = f"fre_{cnpj}_{fre_year}"
            chunk["filename"] = f"{display_name} (FRE {fre_year})"
            chunk["source"] = "CVM/FRE"
            chunk["cnpj"] = cnpj
            # Preserve section number and label from fre_client output
            chunk["section"] = fre_page.get("section", "")
            chunk["section_label"] = fre_page.get("section_label", "")
        fre_chunks.extend(section_chunks)

    all_chunks = doc_chunks + fre_chunks

    # ------------------------------------------------------------------
    # Step 5 — Embed + store
    # ------------------------------------------------------------------
    await emit(f"Indexando {len(all_chunks)} chunks no vector store "
               f"({len(doc_chunks)} DFP/ITR + {len(fre_chunks)} FRE)...")

    start_index = len(storage.chunks)
    storage.chunks.extend(all_chunks)

    chunk_texts = [c["text"] for c in all_chunks]
    chunk_indices = list(range(start_index, start_index + len(all_chunks)))

    vectors = await embed_texts(chunk_texts)
    vector_store.add(vectors, chunk_indices)
    bm25_index.add(chunk_texts, chunk_indices)

    # Register both documents separately so citations show the right source
    storage.documents[f"cvm_{cnpj}"] = {
        "filename": f"{display_name} (CVM)",
        "pages": pages,
        "source": "CVM",
        "cnpj": cnpj,
    }
    if fre_pages:
        storage.documents[f"fre_{cnpj}_{fre_year}"] = {
            "filename": f"{display_name} (FRE {fre_year})",
            "pages": fre_pages,
            "source": "CVM/FRE",
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
        "pages_fetched": len(pages) + len(fre_pages),
        "chunks_added": len(all_chunks),
        "chunk_indices": chunk_indices,
        "dfp_years": dfp_years,
        "itr_years": itr_years,
        "fre_years": [fre_year] if fre_pages else [],
    }
    _save_metadata(metadata)
    save_state()

    await emit(
        f"Concluído! {display_name}: "
        f"{len(pages)} demonstrações financeiras + {len(fre_pages)} seções FRE → "
        f"{len(all_chunks)} chunks indexados."
    )

    return {
        "company": company,
        "scraped": True,
        "pages_fetched": len(pages) + len(fre_pages),
        "chunks_added": len(all_chunks),
        "error": None,
    }
