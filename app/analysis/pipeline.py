"""
Phase 7 — Orchestration.

generate_credit_analysis() ties Phases 1-6 into one pipeline entrypoint:
resolve the company, make sure its CVM/FRE data is indexed, build (or reuse) a
CompanyDossier, run the section generators with the matching sector playbook,
compose the 1-pager + memo, run the reviewer, and persist the AnalysisRun.

Progress is reported via the same async-callback pattern as
app.scraper.pipeline.scrape_and_ingest(), so this can be streamed over SSE the
same way /query/stream is.
"""

import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from app.analysis.composer import compose_memo, compose_one_pager
from app.analysis.dossier_builder import build_dossier
from app.analysis.playbooks import load_playbook
from app.analysis.reviewer import build_analysis_run, persist_analysis_run
from app.analysis.schemas import AnalysisRun, CompanyDossier
from app.analysis.sections import generate_all_sections
from app.scraper.pipeline import scrape_and_ingest

logger = logging.getLogger(__name__)

DOSSIER_DIR = Path(__file__).parent.parent.parent / "data" / "dossiers"

ProgressCallback = Callable[[str], Awaitable[None]]


async def _noop(_: str) -> None:
    pass


def _cnpj_digits(cnpj: str) -> str:
    return re.sub(r"[./-]", "", cnpj)


async def _load_or_build_dossier(cnpj: str, force_rebuild: bool) -> CompanyDossier:
    """Reuse the cached Dossier unless new data was just scraped (or no
    cache exists yet) — rebuilding re-runs ~18 FRE extraction LLM calls."""
    path = DOSSIER_DIR / f"{_cnpj_digits(cnpj)}.json"
    if not force_rebuild and path.exists():
        return CompanyDossier.model_validate_json(path.read_text(encoding="utf-8"))
    return await build_dossier(cnpj)


async def generate_credit_analysis(
    company_query: str,
    year: int | None = None,
    progress: ProgressCallback | None = None,
) -> AnalysisRun:
    """
    Full pipeline: resolve company → ensure data is indexed → build Dossier →
    run section generators → compose 1-pager/memo → review → persist.

    Raises:
        ValueError: company not found in the CVM registry.
        RuntimeError: data could not be fetched from CVM.
    """
    emit = progress or _noop

    # ------------------------------------------------------------------
    # Steps 1-2 — Resolve company + ensure data is indexed (DFP/ITR + FRE)
    # scrape_and_ingest() resolves the company itself and reports progress,
    # so it covers Phase 7.1's steps 1 and 2 in one call.
    # ------------------------------------------------------------------
    scraped = await scrape_and_ingest(company_query, progress=emit, requested_year=year)
    if scraped.get("error"):
        if scraped["company"] is None:
            raise ValueError(scraped["error"])
        raise RuntimeError(scraped["error"])

    company = scraped["company"]
    cnpj = company["cnpj"]
    display_name = company["trade_name"] or company["name"]

    # ------------------------------------------------------------------
    # Step 3 — Build (or reuse) the Company Dossier
    # ------------------------------------------------------------------
    await emit(f"Montando dossiê de {display_name}...")
    dossier = await _load_or_build_dossier(cnpj, force_rebuild=scraped["scraped"])
    await emit(
        f"Dossiê pronto: {len(dossier.financial_line_items)} itens financeiros, "
        f"{len(dossier.qualitative_facts)} fatos qualitativos, "
        f"{len(dossier.disclosed_metrics)} métricas divulgadas."
    )

    # Step 4 — Calculation Engine (Phase 2) is deferred for v1, skipped.

    # ------------------------------------------------------------------
    # Step 5 — Sector playbook
    # ------------------------------------------------------------------
    playbook = load_playbook(dossier.sector)

    # ------------------------------------------------------------------
    # Step 6 — Section generators
    # ------------------------------------------------------------------
    await emit("Gerando seções da análise de crédito (pode levar alguns minutos)...")
    sections = await generate_all_sections(dossier, playbook)
    await emit(f"{len(sections)} seções geradas.")

    # ------------------------------------------------------------------
    # Step 7 — Compose 1-pager + memo
    # ------------------------------------------------------------------
    await emit("Compondo resumo de 1 página e memo completo...")
    one_pager = compose_one_pager(dossier, sections)
    memo = compose_memo(dossier, sections)

    # ------------------------------------------------------------------
    # Step 8 — Review (citation coverage, consistency, confidence)
    # ------------------------------------------------------------------
    await emit("Executando revisão de qualidade...")
    run = build_analysis_run(dossier, sections, one_pager, memo)

    # ------------------------------------------------------------------
    # Step 9 — Persist
    # ------------------------------------------------------------------
    out_dir = persist_analysis_run(run)
    await emit(
        f"Análise concluída. Confiança: {run.confidence_score:.0%} "
        f"({len(run.error_log)} itens no log de revisão). Salvo em {out_dir}."
    )

    return run
