"""
Credit Committee 1-Pager — Pipeline Orchestrator (Pipeline B).

Runs 4 purpose-built agents against a CompanyDossier and composes the
Itaú-style credit committee template.  Pipeline A (9-agent memo) is
completely untouched.
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from app.analysis.committee.agents import (
    run_consolidado_agent,
    run_header_agent,
    run_holding_agent,
    run_perspectivas_agent,
)
from app.analysis.committee.composer import (
    compose_committee_template_pt,
    translate_to_en,
)
from app.analysis.committee.schemas import BankContext, CommitteeReport
from app.analysis.dossier_builder import build_dossier
from app.analysis.schemas import CompanyDossier
from app.scraper.market_data import fetch_market_data, format_market_context, resolve_ticker
from app.scraper.pipeline import scrape_and_ingest

logger = logging.getLogger(__name__)

_INTER_AGENT_DELAY = 2.0

DOSSIER_DIR = Path("data/dossiers")
COMMITTEE_DIR = Path("data/committee_reports")

ProgressCallback = Callable[[str, str], Awaitable[None]]


async def _noop(_type: str, _msg: str) -> None:
    pass


def _cnpj_digits(cnpj: str) -> str:
    return re.sub(r"[./-]", "", cnpj)


# ---------------------------------------------------------------------------
# BankContext persistence
# ---------------------------------------------------------------------------


def load_bank_context(cnpj: str) -> BankContext:
    path = COMMITTEE_DIR / _cnpj_digits(cnpj) / "bank_context.json"
    if path.exists():
        return BankContext.model_validate_json(path.read_text(encoding="utf-8"))
    return BankContext()


def save_bank_context(cnpj: str, ctx: BankContext) -> None:
    path = COMMITTEE_DIR / _cnpj_digits(cnpj) / "bank_context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ctx.model_dump_json(indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CommitteeReport persistence
# ---------------------------------------------------------------------------


def save_committee_report(report: CommitteeReport) -> Path:
    out_dir = COMMITTEE_DIR / _cnpj_digits(report.cnpj) / "latest"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "report_pt.md").write_text(report.report_pt_md, encoding="utf-8")
    (out_dir / "report_en.md").write_text(report.report_en_md, encoding="utf-8")
    return out_dir


def load_committee_report(cnpj: str) -> CommitteeReport | None:
    path = COMMITTEE_DIR / _cnpj_digits(cnpj) / "latest" / "report.json"
    if path.exists():
        return CommitteeReport.model_validate_json(path.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------------------
# Dossier loading (reuses Pipeline A cache — no double-scraping)
# ---------------------------------------------------------------------------


async def _load_or_build_dossier(cnpj: str, force_rebuild: bool) -> CompanyDossier:
    path = DOSSIER_DIR / f"{_cnpj_digits(cnpj)}.json"
    if not force_rebuild and path.exists():
        return CompanyDossier.model_validate_json(path.read_text(encoding="utf-8"))
    return await build_dossier(cnpj)


# ---------------------------------------------------------------------------
# Market context (best-effort, non-blocking)
# ---------------------------------------------------------------------------


async def _fetch_market_context(trade_name: str) -> str:
    ticker = resolve_ticker(trade_name)
    if not ticker:
        return ""
    try:
        data = await asyncio.to_thread(fetch_market_data, ticker)
        return format_market_context(data)
    except Exception as exc:
        logger.warning("Market data fetch failed for %s: %s", trade_name, exc)
        return ""


# ---------------------------------------------------------------------------
# Period label inference
# ---------------------------------------------------------------------------


def _infer_period_label(dossier: CompanyDossier) -> str:
    """Return the most recent period_label from financial_line_items."""
    if not dossier.financial_line_items:
        return "N/A"
    import re as _re

    def _sort_key(label: str) -> tuple:
        m = _re.match(r"FY (\d{4})", label)
        if m:
            return (int(m.group(1)), 99)
        m = _re.match(r"(\d+)M (\d{4})", label)
        if m:
            return (int(m.group(2)), int(m.group(1)))
        return (0, 0)

    labels = {li.period_label for li in dossier.financial_line_items}
    return max(labels, key=_sort_key)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def generate_committee_report(
    company_name: str,
    emit: ProgressCallback | None = None,
    bank_context_override: BankContext | None = None,
) -> CommitteeReport:
    """
    Pipeline B: resolve company → build/load dossier → run 4 agents →
    compose PT template → translate to EN → persist.

    emit(event_type, message) where event_type is "progress" or "result".
    """
    _emit = emit or _noop

    # Step 1 — resolve company + ensure CVM/FRE data is indexed
    await _emit("progress", f"Searching for '{company_name}' in CVM registry...")
    scraped = await scrape_and_ingest(
        company_name,
        progress=lambda msg: asyncio.ensure_future(_emit("progress", msg)),
    )
    if scraped.get("error"):
        if scraped["company"] is None:
            raise ValueError(scraped["error"])
        raise RuntimeError(scraped["error"])

    company = scraped["company"]
    cnpj = company["cnpj"]
    display_name = company["trade_name"] or company["name"]

    # Step 2 — build / load dossier (shared cache with Pipeline A)
    await _emit("progress", f"Building dossier for {display_name}...")
    dossier = await _load_or_build_dossier(cnpj, force_rebuild=scraped["scraped"])
    await _emit(
        "progress",
        f"Dossier ready: {len(dossier.financial_line_items)} financial items, "
        f"{len(dossier.qualitative_facts)} qualitative facts.",
    )

    # Step 3 — Yahoo market data (best-effort)
    await _emit("progress", "Fetching market data...")
    market_ctx = await _fetch_market_context(display_name)

    # Step 4 — load + merge BankContext
    bank_context = load_bank_context(cnpj)
    if bank_context_override:
        bank_context = bank_context.model_copy(
            update=bank_context_override.model_dump(exclude_none=True)
        )

    # Step 5 — Header agent
    await _emit("progress", "Generating header & credit opinion...")
    header_output = await run_header_agent(dossier, market_ctx, bank_context)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 6 — Highlights Consolidado
    await _emit("progress", "Generating highlights — consolidated...")
    consolidado = await run_consolidado_agent(dossier)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 7 — Highlights Holding
    await _emit("progress", "Generating highlights — holding...")
    holding = await run_holding_agent(dossier)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 8 — Perspectivas
    await _emit("progress", "Generating outlook...")
    perspectivas = await run_perspectivas_agent(dossier, market_ctx)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 9 — Financial table (pure Python, no LLM)
    from app.analysis.committee.composer import _build_financial_table

    financial_table = _build_financial_table(dossier, bank_context)

    # Step 10 — Compose Portuguese template
    await _emit("progress", "Composing Portuguese template...")
    report_pt = compose_committee_template_pt(
        dossier, header_output, consolidado, holding, perspectivas, financial_table, bank_context
    )

    # Step 11 — Translate to English
    await _emit("progress", "Translating to English...")
    report_en = await translate_to_en(report_pt)

    # Step 12 — Assemble + persist
    report = CommitteeReport(
        cnpj=cnpj,
        name=dossier.name,
        trade_name=dossier.trade_name,
        period_label=_infer_period_label(dossier),
        generated_at=datetime.now(),
        bank_context=bank_context,
        header_output=header_output,
        highlights_consolidado=consolidado,
        highlights_holding=holding,
        perspectivas=perspectivas,
        financial_table=financial_table,
        report_pt_md=report_pt,
        report_en_md=report_en,
    )
    save_committee_report(report)
    save_bank_context(cnpj, bank_context)

    await _emit("result", report.model_dump_json())
    return report
