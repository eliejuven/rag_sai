"""
B3 Company Coverage Validation
================================
Tests how many companies in the TICKER_MAP can be:
  Tier 1 — resolved to a CNPJ via the CVM registry (name/acronym recognition)
  Tier 2 — financial data scraped from CVM (DFP statements exist for the company)

The DFP ZIP files are shared across all companies (one file covers all ~500 listed
companies), so they are downloaded once and reused — Tier 2 is fast after the
first company triggers the download.

Output: data/validation_b3_coverage.csv
Run:    python3 test_b3_coverage.py
"""

import csv
import random
import sys
import time
from pathlib import Path

from app.scraper.cvm_registry import lookup_company
from app.scraper.cvm_client import fetch_statements
from app.scraper.market_data import TICKER_MAP

OUTPUT_PATH = Path("data/validation_b3_coverage.csv")

MAX_COMPANIES = 300
RANDOM_SEED = 42  # change to get a different sample


# ---------------------------------------------------------------------------
# Build randomised company list — name as input, not ticker
# ---------------------------------------------------------------------------

import re as _re

def _looks_like_ticker(name: str) -> bool:
    """Return True if the key is a ticker code (e.g. 'klbn3', 'PETR4') rather than
    a real company name. Tickers end in a digit and are short (≤ 8 chars)."""
    return bool(_re.fullmatch(r"[a-z]{1,6}\d{1,2}", name.lower()))


def _build_company_list() -> list[tuple[str, str]]:
    """
    Return [(name_as_typed, ticker), ...] — MAX_COMPANIES random unique companies.
    The name is what a user would type (e.g. "petrobras", "ambev", "btg pactual").
    Deduplicates by ticker; skips keys that are themselves ticker codes.
    """
    seen: dict[str, str] = {}  # ticker → first real name
    for name, ticker in TICKER_MAP.items():
        if ticker not in seen and not _looks_like_ticker(name):
            seen[ticker] = name

    unique = list(seen.items())  # [(ticker, name)]
    random.seed(RANDOM_SEED)
    sample = random.sample(unique, min(MAX_COMPANIES, len(unique)))
    return sorted([(name, ticker) for ticker, name in sample], key=lambda x: x[0])


# ---------------------------------------------------------------------------
# Tier 1 — CVM registry lookup
# ---------------------------------------------------------------------------

def tier1_registry(name: str) -> tuple[bool, str, str, str]:
    """
    Returns (passed, cnpj, official_name, failure_reason).
    Uses the same lookup_company() the pipeline uses.
    """
    try:
        result = lookup_company(name)
        if result:
            return True, result["cnpj"], result["trade_name"] or result["name"], ""
        return False, "", "", "not found in CVM registry"
    except Exception as exc:
        return False, "", "", f"error: {exc}"


# ---------------------------------------------------------------------------
# Tier 2 — CVM data scrape
# ---------------------------------------------------------------------------

def tier2_scrape(cnpj: str, official_name: str) -> tuple[bool, str]:
    """
    Returns (passed, failure_reason).
    Calls fetch_statements() for the 2 most recent DFP years only (no ITR)
    to keep the validation fast.  The DFP ZIP is cached after the first call.
    """
    try:
        from datetime import date
        current_year = date.today().year
        pages = fetch_statements(
            cnpj=cnpj,
            company_name=official_name,
            dfp_years=[current_year - 1, current_year - 2],
            itr_years=[],  # skip ITR for speed
        )
        if pages:
            return True, ""
        return False, "no DFP data found for this CNPJ in CVM"
    except Exception as exc:
        return False, f"error: {exc}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    companies = _build_company_list()
    total = len(companies)
    print(f"Testing {total} unique B3 companies (capped at {MAX_COMPANIES})\n")

    rows: list[dict] = []
    tier1_pass = 0
    tier2_pass = 0

    for i, (name, ticker) in enumerate(companies, start=1):
        print(f"[{i:02d}/{total}] {name:<35} ({ticker})", end="  ", flush=True)

        # --- Tier 1 ---
        t1_ok, cnpj, official_name, t1_reason = tier1_registry(name)
        tier1_label = "PASS" if t1_ok else "FAIL"
        if t1_ok:
            tier1_pass += 1

        # --- Tier 2 (only if Tier 1 passed) ---
        if t1_ok:
            t2_ok, t2_reason = tier2_scrape(cnpj, official_name)
            tier2_label = "PASS" if t2_ok else "FAIL"
            if t2_ok:
                tier2_pass += 1
        else:
            tier2_label = "SKIP"
            t2_reason = "skipped (Tier 1 failed)"

        print(f"T1={tier1_label}  T2={tier2_label}", end="")
        if not t1_ok:
            print(f"  ← {t1_reason}", end="")
        elif not t2_ok:
            print(f"  ← {t2_reason}", end="")
        print()

        rows.append({
            "company_name": name,
            "ticker": ticker,
            "tier1_registry": tier1_label,
            "tier1_cnpj": cnpj,
            "tier1_official_name": official_name,
            "tier1_failure": t1_reason,
            "tier2_cvm_data": tier2_label,
            "tier2_failure": t2_reason,
        })

        # Small pause between companies to avoid hammering CVM
        time.sleep(0.2)

    # --- Write CSV ---
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # --- Summary ---
    tier2_eligible = tier1_pass
    print(f"""
{'='*60}
RESULTS
{'='*60}
  Total companies tested : {total}
  Tier 1 PASS (registry) : {tier1_pass}/{total}  ({tier1_pass/total*100:.0f}%)
  Tier 2 PASS (CVM data) : {tier2_pass}/{tier2_eligible} eligible  ({tier2_pass/tier2_eligible*100:.0f}% of Tier 1 passes)

  Full report saved to   : {OUTPUT_PATH}
""")

    # Exit with non-zero if any company failed Tier 1
    if tier1_pass < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
