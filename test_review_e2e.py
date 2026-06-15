"""
Phase 6 — Review/Critic real-data sanity check.

Runs the full v1 agent roster (Phase 4) on the cached Vale dossier, composes
the memo/one-pager (Phase 5), then runs the Phase 6 review (citation
coverage, internal consistency, confidence score) and persists the full
AnalysisRun under data/analysis_runs/.

Usage: python3 test_review_e2e.py
"""

import asyncio

from app.analysis.composer import compose_memo, compose_one_pager
from app.analysis.playbooks import load_playbook
from app.analysis.reviewer import build_analysis_run, persist_analysis_run
from app.analysis.schemas import CompanyDossier
from app.analysis.sections import generate_all_sections
from app.persistence import load_state

DOSSIER_PATH = "data/dossiers/33592510000154.json"  # Vale


async def main():
    load_state()

    dossier = CompanyDossier.model_validate_json(open(DOSSIER_PATH, encoding="utf-8").read())
    playbook = load_playbook(dossier.sector)

    sections = await generate_all_sections(dossier, playbook)

    memo = compose_memo(dossier, sections)
    one_pager = compose_one_pager(dossier, sections)

    run = build_analysis_run(dossier, sections, one_pager, memo)
    out_dir = persist_analysis_run(run)

    print(f"persisted -> {out_dir}")
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
    for e in run.error_log:
        print(f"  [{e.severity}/{e.stage}] {e.message} ({e.location})")


if __name__ == "__main__":
    asyncio.run(main())
