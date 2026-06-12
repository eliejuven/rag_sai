"""
Company Dossier Builder

build_dossier(cnpj) assembles one CompanyDossier per company from everything
already scraped and stored in app.storage:
  - DFP/ITR financial line items (structured, code-based — see
    app.scraper.cvm_client's line_items addition).
  - FRE qualitative facts and disclosed metrics (LLM-extracted — see
    app.analysis.extraction).

The result is persisted to data/dossiers/<cnpj_digits>.json so downstream
section generators (Phase 4) can load it without rebuilding. Every section
generator reads the FULL Dossier — there is no per-agent slicing.
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from app import storage
from app.analysis.extraction import (
    DISCLOSED_METRICS_SECTION,
    extract_disclosed_metrics,
    extract_qualitative_facts,
)
from app.analysis.schemas import (
    Citation,
    CompanyDossier,
    DisclosedMetric,
    DossierCoverage,
    FactConflict,
    FinancialLineItem,
    QualitativeFact,
)
from app.scraper.cvm_registry import get_company_by_cnpj
from app.scraper.fre_client import CREDIT_SECTIONS

logger = logging.getLogger(__name__)

DOSSIER_DIR = Path(__file__).parent.parent.parent / "data" / "dossiers"

# Flag a FactConflict when the same account_code + period_label differs by
# more than this fraction between sources (placeholder threshold, see
# TODO.md Open Questions #4).
_CONFLICT_THRESHOLD = 0.01

# Cap concurrent LLM extraction calls — a company can have up to 18 FRE
# sections, and firing them all at once triggers Mistral 429s.
_MAX_CONCURRENT_EXTRACTIONS = 3


def _cnpj_digits(cnpj: str) -> str:
    return re.sub(r"[./-]", "", cnpj)


def _build_financial_line_items(document_id: str) -> list[FinancialLineItem]:
    """Flatten line_items from every DFP/ITR page into FinancialLineItems.

    Companies scraped before line_items was added to cvm_client.py simply
    have no line_items per page — they yield no entries here until re-scraped.
    """
    doc = storage.documents.get(document_id)
    if not doc:
        return []

    items = []
    for page in doc["pages"]:
        citation = Citation(
            document_id=document_id,
            filename=doc["filename"],
            page_number=page.get("page_number"),
        )
        for li in page.get("line_items", []):
            items.append(
                FinancialLineItem(
                    account_code=li["account_code"],
                    description=li["description"],
                    value=li["value"],
                    scale=li["scale"],
                    period_label=li["period_label"],
                    statement_type=li["statement_type"],
                    citation=citation,
                )
            )
    return items


def _find_conflicts(line_items: list[FinancialLineItem]) -> list[FactConflict]:
    """Flag account_code + period_label combos with materially different
    values across sources/statement types (e.g. DFP vs. restated ITR)."""
    groups: dict[tuple[str, str], list[FinancialLineItem]] = {}
    for li in line_items:
        groups.setdefault((li.account_code, li.period_label), []).append(li)

    conflicts = []
    for (account_code, period_label), items in groups.items():
        if len(items) < 2:
            continue

        base = items[0].value
        for other in items[1:]:
            if base == 0:
                continue
            rel_diff = abs(other.value - base) / abs(base)
            if rel_diff > _CONFLICT_THRESHOLD:
                conflicts.append(
                    FactConflict(
                        description=(
                            f"Conta {account_code} ({items[0].description}) "
                            f"para o período {period_label} difere entre fontes"
                        ),
                        values=[(i.value, i.citation) for i in items],
                    )
                )
                break

    return conflicts


def _coverage_years(line_items: list[FinancialLineItem]) -> tuple[list[int], list[int]]:
    """Split FinancialLineItems into DFP (annual) and ITR (quarterly) years
    based on period_label ("FY 2024" vs. "3M 2025 (Jan–Mar)" etc.)."""
    dfp_years: set[int] = set()
    itr_years: set[int] = set()

    for li in line_items:
        match = re.search(r"\d{4}", li.period_label)
        if not match:
            continue
        year = int(match.group())
        if li.period_label.startswith("FY"):
            dfp_years.add(year)
        else:
            itr_years.add(year)

    return sorted(dfp_years), sorted(itr_years)


async def _extract_fre_page(
    page: dict, document_id: str, filename: str, fre_year: int, semaphore: asyncio.Semaphore
) -> tuple[list[QualitativeFact], list[DisclosedMetric]]:
    section = page.get("section", "")
    section_label = page.get("section_label", "")
    citation = Citation(
        document_id=document_id,
        filename=filename,
        section=section,
        section_label=section_label,
        page_number=page.get("page_number"),
    )

    async with semaphore:
        if section == DISCLOSED_METRICS_SECTION:
            metrics = await extract_disclosed_metrics(page["text"], f"FY {fre_year}", citation)
            return [], metrics

        facts = await extract_qualitative_facts(section, section_label, page["text"], citation)
        return facts, []


async def build_dossier(cnpj: str) -> CompanyDossier:
    """Build (and persist) a CompanyDossier from everything scraped for cnpj."""
    company = get_company_by_cnpj(cnpj) or {}

    financial_line_items = _build_financial_line_items(f"cvm_{cnpj}")
    conflicts = _find_conflicts(financial_line_items)
    dfp_years, itr_years = _coverage_years(financial_line_items)

    # Pull every FRE document ever scraped for this CNPJ (the store is
    # append-only, so multiple reference years may be present).
    fre_prefix = f"fre_{cnpj}_"
    fre_jobs = []
    fre_years: set[int] = set()
    fre_sections_present: set[str] = set()
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_EXTRACTIONS)

    for document_id, doc in storage.documents.items():
        if not document_id.startswith(fre_prefix):
            continue
        fre_year = int(document_id.removeprefix(fre_prefix))
        fre_years.add(fre_year)

        for page in doc["pages"]:
            fre_sections_present.add(page.get("section", ""))
            fre_jobs.append(_extract_fre_page(page, document_id, doc["filename"], fre_year, semaphore))

    qualitative_facts: list[QualitativeFact] = []
    disclosed_metrics: list[DisclosedMetric] = []
    for facts, metrics in await asyncio.gather(*fre_jobs):
        qualitative_facts.extend(facts)
        disclosed_metrics.extend(metrics)

    fre_sections_missing = sorted(set(CREDIT_SECTIONS) - fre_sections_present)

    coverage = DossierCoverage(
        dfp_years=dfp_years,
        itr_years=itr_years,
        fre_years=sorted(fre_years),
        fre_sections_present=sorted(fre_sections_present),
        fre_sections_missing=fre_sections_missing,
    )

    dossier = CompanyDossier(
        cnpj=cnpj,
        cd_cvm=company.get("cd_cvm", ""),
        name=company.get("name", ""),
        trade_name=company.get("trade_name", ""),
        sector=company.get("sector") or None,
        generated_at=datetime.now(),
        financial_line_items=financial_line_items,
        disclosed_metrics=disclosed_metrics,
        qualitative_facts=qualitative_facts,
        conflicts=conflicts,
        coverage=coverage,
    )

    _persist_dossier(cnpj, dossier)
    return dossier


def _persist_dossier(cnpj: str, dossier: CompanyDossier) -> None:
    DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    path = DOSSIER_DIR / f"{_cnpj_digits(cnpj)}.json"
    path.write_text(dossier.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Dossier for %s persisted to %s", cnpj, path)
