"""
Test script for Phase 3 — Sector Playbook loader.

Checks: _slugify() against real CVM SETOR_ATIV values (including the
"Emp. Adm. Part. - <X>" holding-company variants, which should slug to the
same value as <X>), and load_playbook() falling back to _default.md when no
sector-specific file exists.

Usage: python3 test_playbooks.py
"""

import pandas as pd

from app.analysis.playbooks import DEFAULT_PLAYBOOK, PLAYBOOK_DIR, _slugify, load_playbook


def main():
    df = pd.read_csv("data/cvm_registry.csv", sep=";", encoding="latin-1")
    sectors = sorted(df["SETOR_ATIV"].dropna().unique())
    print(f"{len(sectors)} distinct SETOR_ATIV values\n")

    for sector in sectors:
        print(f"{sector!r:60s} -> {_slugify(sector)!r}")

    # Holding-company variants ("Emp. Adm. Part. - <X>") should slug to the
    # same value as the underlying sector <X>, so both share one playbook.
    base_slugs = {_slugify(s) for s in sectors if not s.startswith("Emp. Adm. Part")}
    print("\n-- holding-company alias checks --")
    for sector in sectors:
        if not sector.startswith("Emp. Adm. Part"):
            continue
        slug = _slugify(sector)
        if not slug:
            continue
        status = "OK" if slug in base_slugs else "NO MATCHING BASE SECTOR"
        print(f"{status}: {sector!r} -> {slug!r}")

    # No underlying sector -> empty slug -> falls back to default
    print("\n-- no-underlying-sector checks --")
    for sector in ["Emp. Adm. Participações", "Emp. Adm. Part. - Sem Setor Principal"]:
        print(f"{sector!r:45s} -> slug={_slugify(sector)!r}")

    # load_playbook fallback behavior
    print("\n-- load_playbook fallback --")
    default_text = (PLAYBOOK_DIR / DEFAULT_PLAYBOOK).read_text(encoding="utf-8")
    for sector in [None, "Petróleo e Gás", "Extração Mineral"]:
        text = load_playbook(sector)
        is_default = text == default_text
        print(f"sector={sector!r:20} -> default playbook used: {is_default}")


if __name__ == "__main__":
    main()
