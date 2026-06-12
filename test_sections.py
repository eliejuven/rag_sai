"""
Test script for Phase 4 — Section generators ("agents").

Loads a cached CompanyDossier (data/dossiers/<cnpj_digits>.json, built by
Phase 1's build_dossier) and the matching sector playbook (Phase 3), loads the
persisted search index (so fallback search — Decision 2 — has something to
search), then runs the full v1 agent roster end-to-end. Prints each
SectionOutput's statements, tagged fact/inference/judgment, with resolved
citations.

Usage: python3 test_sections.py
"""

import asyncio

from app.analysis.playbooks import load_playbook
from app.analysis.schemas import CompanyDossier, SectionOutput
from app.analysis.sections import generate_all_sections
from app.persistence import load_state

DOSSIER_PATH = "data/dossiers/33592510000154.json"  # Vale


def _print_section(section: SectionOutput) -> None:
    print(f"\n--- {section.section_id} — {section.title} ---")
    if not section.statements:
        print("  (nenhuma statement gerada)")
        return
    for s in section.statements:
        print(f"  [{s.type}] {s.text}")
        if s.citations:
            cites = ", ".join(
                f"{c.document_id}#{c.section or c.page_number}" for c in s.citations
            )
            print(f"      citações: {cites}")
        if s.derived_from:
            print(f"      derived_from: {s.derived_from}")
        if s.basis:
            print(f"      basis: {s.basis}")


async def main():
    load_state()

    dossier = CompanyDossier.model_validate_json(open(DOSSIER_PATH, encoding="utf-8").read())
    playbook = load_playbook(dossier.sector)

    print(f"{dossier.name} ({dossier.trade_name}) — setor: {dossier.sector}")
    print(
        f"financial_line_items={len(dossier.financial_line_items)} "
        f"qualitative_facts={len(dossier.qualitative_facts)} "
        f"disclosed_metrics={len(dossier.disclosed_metrics)}"
    )
    print(f"coverage: {dossier.coverage.model_dump()}")

    sections = await generate_all_sections(dossier, playbook)

    for section in sections:
        _print_section(section)

    total = sum(len(s.statements) for s in sections)
    with_citations = sum(1 for s in sections for st in s.statements if st.citations)
    print(f"\n{'=' * 70}")
    print(f"{len(sections)} sections, {total} statements total, {with_citations} with citations")


if __name__ == "__main__":
    asyncio.run(main())
