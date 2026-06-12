"""
Test script for Phase 1 — Company Dossier builder.

Refreshes DFP/ITR pages in-memory (to populate line_items, added after these
companies were originally scraped), then builds a CompanyDossier for
companies that already have full DFP+ITR+FRE coverage in storage.

Usage: python3 test_dossier.py
"""

import asyncio

from app import storage
from app.analysis.dossier_builder import build_dossier
from app.persistence import load_state
from app.scraper.cvm_client import fetch_statements

COMPANIES = [
    ("33.592.510/0001-54", "VALE", [2024, 2025], [2025, 2026]),
    ("33.000.167/0001-01", "PETROBRAS", [2024, 2025], [2025, 2026]),
]


def _refresh_line_items(cnpj: str, company_name: str, dfp_years, itr_years) -> None:
    """Re-run fetch_statements (cached ZIPs, no network) to populate
    line_items on pages scraped before this field existed."""
    pages = fetch_statements(cnpj, company_name, dfp_years=dfp_years, itr_years=itr_years)
    doc_id = f"cvm_{cnpj}"
    if doc_id in storage.documents:
        storage.documents[doc_id]["pages"] = pages


async def main():
    load_state()

    for cnpj, name, dfp_years, itr_years in COMPANIES:
        print(f"\n{'=' * 70}\n{name} ({cnpj})\n{'=' * 70}")
        _refresh_line_items(cnpj, name, dfp_years, itr_years)

        dossier = await build_dossier(cnpj)

        print(f"name: {dossier.name} | trade_name: {dossier.trade_name} | sector: {dossier.sector}")
        print(f"financial_line_items: {len(dossier.financial_line_items)}")
        print(f"disclosed_metrics: {len(dossier.disclosed_metrics)}")
        print(f"qualitative_facts: {len(dossier.qualitative_facts)}")
        print(f"conflicts: {len(dossier.conflicts)}")
        print(f"coverage: {dossier.coverage.model_dump()}")

        if dossier.financial_line_items:
            print("\n-- sample financial line item --")
            print(dossier.financial_line_items[0].model_dump_json(indent=2))

        if dossier.disclosed_metrics:
            print("\n-- sample disclosed metric --")
            print(dossier.disclosed_metrics[0].model_dump_json(indent=2))

        if dossier.qualitative_facts:
            print("\n-- sample qualitative fact --")
            print(dossier.qualitative_facts[0].model_dump_json(indent=2))

        if dossier.conflicts:
            print("\n-- sample conflict --")
            print(dossier.conflicts[0].model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
