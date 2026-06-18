"""
Phase D — Output number cross-check.

After the PT markdown is composed, extract all numeric values and check each
against the dossier pool.  Unverified numbers surface as a warning in the SSE
stream without blocking generation.

This is a best-effort signal: the tolerance is intentionally generous to reduce
false positives from rounding and unit expression differences (e.g. "R$ 497
bilhões" vs 497,549 MM).  The analyst uses the count to decide whether to do a
manual spot-check.
"""

import re
from dataclasses import dataclass, field

from app.analysis.committee.fact_sheet import FactSheet
from app.analysis.schemas import CompanyDossier

# 15% relative tolerance for large MM figures — allows for billion rounding,
# e.g. "R$ 490 bilhões" vs actual 497.549 (1.5% off) → verified.
# Would NOT match "R$ 400 bilhões" (19.6% off) → flagged correctly.
_REL_TOL_LARGE = 0.15

# 2.0 absolute tolerance for small values (%, ratios, small billions)
# Handles "margem de 44%" vs pool 44.0, "1.3x" vs pool 1.29, etc.
_ABS_TOL_SMALL = 2.0

# Skip numbers below this threshold — catches footnote superscripts rendered as
# plain digits, section references like "2" or "4", etc.
_MIN_VALUE = 0.4

# Numbers in this range are calendar years — never financial figures.
_YEAR_MIN, _YEAR_MAX = 1900, 2100


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    verified: int = 0
    unverified: list[str] = field(default_factory=list)

    @property
    def warning(self) -> str | None:
        n = len(self.unverified)
        if n == 0:
            return None
        samples = ", ".join(self.unverified[:5])
        suffix = f" and {n - 5} more" if n > 5 else ""
        return (
            f"⚠ {n} number(s) in the report could not be verified against "
            f"the dossier: {samples}{suffix}"
        )


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------


def _add_mm(pool: list[float], v: float | None) -> None:
    """Add a MM value and its billion-scale equivalent to the pool."""
    if v is None:
        return
    v = abs(v)
    pool.append(v)           # as MM
    pool.append(v / 1_000)  # as billions (so "497 bilhões" matches 497,549 MM)


def _add_ratio(pool: list[float], v: float | None) -> None:
    if v is not None:
        pool.append(abs(v))


def _build_pool(fact_sheet: FactSheet, dossier: CompanyDossier) -> list[float]:
    pool: list[float] = []

    # FactSheet — MM values
    for v in (
        fact_sheet.revenue_latest_mm,
        fact_sheet.revenue_prior_mm,
        fact_sheet.ebitda_latest_mm,
        fact_sheet.ebitda_prior_mm,
        fact_sheet.net_debt_latest_mm,
        fact_sheet.net_debt_prior_mm,
        fact_sheet.revenue_yoy_abs_mm,
        fact_sheet.net_debt_yoy_abs_mm,
    ):
        _add_mm(pool, v)

    # FactSheet — ratios and percentages (no scale conversion)
    for v in (
        fact_sheet.ebitda_margin_latest_pct,
        fact_sheet.ebitda_margin_prior_pct,
        fact_sheet.leverage_latest_x,
        fact_sheet.leverage_prior_x,
        fact_sheet.revenue_yoy_pct,
    ):
        _add_ratio(pool, v)

    # Disclosed metrics (already in display units: MM, %, ratios)
    for m in dossier.disclosed_metrics:
        if m.value is None:
            continue
        v = abs(m.value)
        pool.append(v)
        if m.unit and "bilh" in m.unit.lower():
            pool.append(v * 1_000)  # bilhões → also add MM equivalent
        else:
            pool.append(v / 1_000)  # also add billion-scale

    # Financial line items (raw R$ → MM and billions)
    for li in dossier.financial_line_items:
        if li.value is None:
            continue
        mm = abs(li.value) / 1_000_000
        pool.append(mm)
        pool.append(mm / 1_000)  # billions scale

    return pool


# ---------------------------------------------------------------------------
# Number extraction and matching
# ---------------------------------------------------------------------------


# Matches EN-format numbers: optional thousands commas, optional decimal.
# Examples: "497,549.0"  "12.5"  "44"  "1.29"
_NUM_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)"
)


def _parse(raw: str) -> list[float]:
    """Return all plausible numeric interpretations of raw.

    Handles:
    - EN format  "12,500.0"  → [12500.0]
    - PT decimal "1,29"      → [129.0, 1.29]   (EN and PT interpretations)
    - PT decimal "2,4"       → [24.0, 2.4]
    """
    results = []

    # EN interpretation: strip commas as thousands separators
    en = raw.replace(",", "")
    try:
        results.append(float(en))
    except ValueError:
        pass

    # PT decimal fallback: if no "." present, comma might be decimal separator
    if "," in raw and "." not in raw:
        pt = raw.replace(",", ".")
        try:
            v = float(pt)
            if not results or abs(v - results[0]) > 0.001:
                results.append(v)
        except ValueError:
            pass

    return results


def _matches(value: float, pool: list[float]) -> bool:
    if value > 100:
        return any(p > 0 and abs(value - p) / p <= _REL_TOL_LARGE for p in pool)
    return any(abs(value - p) <= _ABS_TOL_SMALL for p in pool)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cross_check(
    report_md: str,
    fact_sheet: FactSheet,
    dossier: CompanyDossier,
) -> VerificationResult:
    """
    Extract numeric tokens from the markdown and verify each against the
    dossier value pool.

    Skips: year numbers (1900–2100), values below _MIN_VALUE.
    Does NOT skip the financial table — those values should also match the pool,
    so including them raises the verified count and surfaces bugs if they don't.
    """
    pool = _build_pool(fact_sheet, dossier)
    result = VerificationResult()

    for m in _NUM_RE.finditer(report_md):
        raw = m.group(1)
        interpretations = _parse(raw)
        if not interpretations:
            continue

        # Use primary (EN) interpretation for filtering
        primary = interpretations[0]
        if primary <= 0:
            continue
        if _YEAR_MIN <= primary <= _YEAR_MAX:
            continue
        if primary < _MIN_VALUE:
            continue

        # Verified if ANY interpretation matches ANY pool value
        if any(_matches(v, pool) for v in interpretations):
            result.verified += 1
        else:
            result.unverified.append(raw)

    return result
