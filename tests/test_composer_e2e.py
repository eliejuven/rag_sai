"""
Phase 5 — Composer real-data sanity check.

Runs the full v1 agent roster (Phase 4) on the cached Vale dossier, then
renders the result with compose_memo() and compose_one_pager() (Phase 5),
writing both to /tmp for inspection.

Usage: python3 test_composer_e2e.py
"""

import asyncio

from app.analysis.composer import compose_memo, compose_one_pager
from app.analysis.playbooks import load_playbook
from app.analysis.schemas import CompanyDossier
from app.analysis.sections import generate_all_sections
from app.persistence import load_state

DOSSIER_PATH = "data/dossiers/33592510000154.json"  # Vale
MEMO_PATH = "/tmp/vale_memo.md"
ONE_PAGER_PATH = "/tmp/vale_one_pager.md"


async def main():
    load_state()

    dossier = CompanyDossier.model_validate_json(open(DOSSIER_PATH, encoding="utf-8").read())
    playbook = load_playbook(dossier.sector)

    sections = await generate_all_sections(dossier, playbook)

    memo = compose_memo(dossier, sections)
    one_pager = compose_one_pager(dossier, sections)

    open(MEMO_PATH, "w", encoding="utf-8").write(memo)
    open(ONE_PAGER_PATH, "w", encoding="utf-8").write(one_pager)

    print(f"memo: {len(memo)} chars -> {MEMO_PATH}")
    print(f"one_pager: {len(one_pager)} chars -> {ONE_PAGER_PATH}")
    print(f"\n{'=' * 30} ONE-PAGER {'=' * 30}\n")
    print(one_pager)


if __name__ == "__main__":
    asyncio.run(main())
