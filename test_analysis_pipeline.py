"""
Test script for Phase 7 — Orchestration.

End-to-end run of generate_credit_analysis() on a company that's already
fully scraped + cached (Vale), so scrape_and_ingest() is a no-op and the
cached Dossier (data/dossiers/33592510000154.json) is reused — the only LLM
calls are Phase 4's section generators (~6 min, sequential due to Mistral
rate limits).

Usage: python3 test_analysis_pipeline.py
"""

import asyncio

from app.analysis.pipeline import generate_credit_analysis
from app.persistence import load_state


async def emit(message: str) -> None:
    print(f"  -> {message}")


async def main():
    load_state()
    run = await generate_credit_analysis("Vale", progress=emit)

    print(f"\ncompany: {run.company_name} ({run.cnpj})")
    print(f"generated_at: {run.generated_at}")
    print(f"sections: {len(run.sections)}")
    print(f"\nconfidence_score: {run.confidence_score}")
    print(f"confidence_breakdown: {run.confidence_breakdown}")

    print(f"\nlimitations ({len(run.limitations)}):")
    for l in run.limitations:
        print(f"  - {l}")

    print(f"\nerror_log ({len(run.error_log)}):")
    by_severity: dict[str, int] = {}
    for e in run.error_log:
        by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
    print(f"  by severity: {by_severity}")

    print(f"\none_pager_md length: {len(run.one_pager_md)} chars")
    print(f"memo_md length: {len(run.memo_md)} chars")


if __name__ == "__main__":
    asyncio.run(main())
