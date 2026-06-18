"""
Pre-computed FactSheet — deterministic computation from CompanyDossier, no LLM.

Key principle: the FactSheet provides derived metrics (YoY revenue deltas, leverage)
that agents would otherwise compute incorrectly.  Agents narrate from these values;
they do not recompute.

Critical invariant: leverage is read from the company's *stated* ratio metric, not
computed by dividing Net Debt by EBITDA.  Those two operands use different EBITDA
bases (the stated ratio uses a further-adjusted figure), so independent computation
gives a materially wrong answer.
"""

import re
from typing import Optional

from pydantic import BaseModel

from app.analysis.schemas import CompanyDossier, DisclosedMetric, ExtractedKPI, FinancialLineItem


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class FactSheet(BaseModel):
    # Most-recent and comparison period labels (both FY when available)
    period_latest: str
    period_prior: Optional[str] = None

    # Revenue (R$ MM) — from account 3.01, DRE_con
    revenue_latest_mm: Optional[float] = None
    revenue_prior_mm: Optional[float] = None
    # YoY figures computed only when BOTH are FY periods — None otherwise
    revenue_yoy_abs_mm: Optional[float] = None
    revenue_yoy_pct: Optional[float] = None

    # EBITDA (R$ MM) — from disclosed metrics; period may differ from revenue
    ebitda_latest_mm: Optional[float] = None
    ebitda_prior_mm: Optional[float] = None
    ebitda_period_latest: Optional[str] = None
    ebitda_period_prior: Optional[str] = None

    # EBITDA Margin (%) — stated in disclosed metrics, never computed from above
    ebitda_margin_latest_pct: Optional[float] = None
    ebitda_margin_prior_pct: Optional[float] = None

    # Net Debt (R$ MM) — from disclosed metrics; period may differ
    net_debt_latest_mm: Optional[float] = None
    net_debt_prior_mm: Optional[float] = None
    net_debt_period_latest: Optional[str] = None
    net_debt_period_prior: Optional[str] = None
    net_debt_yoy_abs_mm: Optional[float] = None

    # Leverage — stated ratio from disclosed metrics, NEVER computed from DL÷EBITDA
    leverage_latest_x: Optional[float] = None
    leverage_prior_x: Optional[float] = None
    leverage_period_latest: Optional[str] = None
    leverage_period_prior: Optional[str] = None
    leverage_label: Optional[str] = None  # e.g. "Dívida Líquida/EBITDA Ajustado"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _period_sort_key(label: str) -> tuple:
    m = re.match(r"FY (\d{4})", label)
    if m:
        return (int(m.group(1)), 99)
    m = re.match(r"(\d+)M (\d{4})", label)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    return (0, 0)


def _is_fy(label: str) -> bool:
    return bool(re.match(r"FY \d{4}$", label))


def _to_mm(value: float, scale: str = "") -> float:
    """Convert a financial line-item value to R$ MM.

    cvm_client pre-multiplies by the ESCALA_MOEDA factor before storing, so
    FinancialLineItem.value is always in raw R$.  MM = raw R$ / 1 000 000.
    """
    return value / 1_000_000  # assume already MM


def _metric_to_mm(m: DisclosedMetric) -> float:
    """Return a disclosed metric value in R$ MM (handles bilhões labels)."""
    if m.unit and "bilh" in m.unit.lower():
        return m.value * 1_000
    return m.value  # already in MM (or unitless for ratios/percentages)


def _kpi_to_mm(kpi: ExtractedKPI) -> float:
    """Normalize an ExtractedKPI value to R$ MM using its unit field."""
    unit = (kpi.unit or "").lower()
    if "bilh" in unit:
        return kpi.value * 1_000
    if "mil" in unit and "milhão" not in unit and "milhões" not in unit:
        return kpi.value / 1_000  # R$ mil → MM
    return kpi.value  # R$ milhões / MM or unitless


def _find_metrics(
    metrics: list[DisclosedMetric],
    *keywords: str,
    exclude: list[str] | None = None,
) -> list[DisclosedMetric]:
    """Return metrics whose label contains ALL keywords and none of the exclusions."""
    exc = [k.lower() for k in (exclude or [])]
    return [
        m for m in metrics
        if all(k.lower() in m.label.lower() for k in keywords)
        and not any(e in m.label.lower() for e in exc)
        and m.value is not None
    ]


def _fy_sorted(candidates: list[DisclosedMetric]) -> list[DisclosedMetric]:
    """Return FY-period metrics newest-first; fall back to all candidates if none are FY."""
    fy = [m for m in candidates if _is_fy(m.period_label)]
    pool = fy if fy else candidates
    return sorted(pool, key=lambda m: _period_sort_key(m.period_label), reverse=True)


def _dedupe_by_period(
    candidates: list[DisclosedMetric],
    prefer_keyword: str = "",
) -> list[DisclosedMetric]:
    """
    Keep at most one metric per period label.  When multiple candidates share
    a period, prefer the one whose label contains `prefer_keyword` (if given).
    This prevents two EBITDA variants (e.g. plain vs. Ajustado) from the same
    period being treated as a "latest vs prior" time comparison.
    """
    by_period: dict[str, DisclosedMetric] = {}
    kw = prefer_keyword.lower()
    for m in candidates:
        p = m.period_label
        if p not in by_period:
            by_period[p] = m
        elif kw and kw in m.label.lower() and kw not in by_period[p].label.lower():
            by_period[p] = m
    return list(by_period.values())


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_fact_sheet(dossier: CompanyDossier) -> FactSheet:
    """
    Build FactSheet deterministically from dossier.  No LLM calls.

    YoY revenue is computed only when both FY periods exist in account 3.01.
    Leverage is read from the stated ratio metric — never computed from DL÷EBITDA.
    """
    items = dossier.financial_line_items
    metrics = dossier.disclosed_metrics

    # ---- Revenue: account 3.01, DRE_con ----
    rev_items = [
        li for li in items
        if li.account_code == "3.01" and li.statement_type == "DRE_con"
    ]
    fy_rev = sorted(
        [li for li in rev_items if _is_fy(li.period_label)],
        key=lambda li: _period_sort_key(li.period_label),
        reverse=True,
    )

    if fy_rev:
        period_latest = fy_rev[0].period_label
        period_prior = fy_rev[1].period_label if len(fy_rev) >= 2 else None
        revenue_latest_mm = round(_to_mm(fy_rev[0].value, fy_rev[0].scale), 1)
        revenue_prior_mm = (
            round(_to_mm(fy_rev[1].value, fy_rev[1].scale), 1)
            if len(fy_rev) >= 2 else None
        )
    elif rev_items:
        all_rev = sorted(
            rev_items, key=lambda li: _period_sort_key(li.period_label), reverse=True
        )
        period_latest = all_rev[0].period_label
        period_prior = None
        revenue_latest_mm = round(_to_mm(all_rev[0].value, all_rev[0].scale), 1)
        revenue_prior_mm = None
    else:
        period_latest = "N/A"
        period_prior = None
        revenue_latest_mm = None
        revenue_prior_mm = None

    revenue_yoy_abs_mm = None
    revenue_yoy_pct = None
    if revenue_latest_mm is not None and revenue_prior_mm is not None and revenue_prior_mm != 0:
        revenue_yoy_abs_mm = round(revenue_latest_mm - revenue_prior_mm, 1)
        revenue_yoy_pct = round(
            (revenue_latest_mm - revenue_prior_mm) / revenue_prior_mm * 100, 1
        )

    ekpis = dossier.extracted_kpis  # LLM-extracted, semantic — preferred source

    # ---- EBITDA: extracted_kpis first, keyword search fallback ----
    ebitda_latest_mm: float | None = None
    ebitda_period_latest: str | None = None
    ebitda_prior_mm: float | None = None
    ebitda_period_prior: str | None = None

    if ekpis and ekpis.ebitda:
        ebitda_latest_mm = round(_kpi_to_mm(ekpis.ebitda), 1)
        ebitda_period_latest = ekpis.ebitda.period
    else:
        ebitda_raw = (
            _find_metrics(metrics, "EBITDA", exclude=["margem", "margin", "/"]) or
            _find_metrics(metrics, "LAJIDA", exclude=["margem", "margin", "/"])
        )
        ebitda_sorted = _fy_sorted(_dedupe_by_period(ebitda_raw, prefer_keyword="ajustado"))
        if ebitda_sorted:
            ebitda_latest_mm = round(_metric_to_mm(ebitda_sorted[0]), 1)
            ebitda_period_latest = ebitda_sorted[0].period_label
            if len(ebitda_sorted) >= 2:
                ebitda_prior_mm = round(_metric_to_mm(ebitda_sorted[1]), 1)
                ebitda_period_prior = ebitda_sorted[1].period_label

    # ---- EBITDA Margin: extracted_kpis first, keyword search fallback ----
    ebitda_margin_latest_pct: float | None = None
    ebitda_margin_prior_pct: float | None = None

    if ekpis and ekpis.ebitda_margin:
        ebitda_margin_latest_pct = ekpis.ebitda_margin.value
    else:
        margem_raw = (
            _find_metrics(metrics, "Margem", "EBITDA") or
            _find_metrics(metrics, "Margem", "LAJIDA")
        )
        margem_sorted = _fy_sorted(_dedupe_by_period(margem_raw))
        if margem_sorted:
            ebitda_margin_latest_pct = margem_sorted[0].value
            if len(margem_sorted) >= 2:
                ebitda_margin_prior_pct = margem_sorted[1].value

    # ---- Net Debt: extracted_kpis first, keyword search fallback ----
    net_debt_latest_mm: float | None = None
    net_debt_period_latest: str | None = None
    net_debt_prior_mm: float | None = None
    net_debt_period_prior: str | None = None

    if ekpis and ekpis.net_debt:
        net_debt_latest_mm = round(_kpi_to_mm(ekpis.net_debt), 1)
        net_debt_period_latest = ekpis.net_debt.period
    else:
        dl_raw = (
            _find_metrics(metrics, "Dívida Líquida", exclude=["ebitda", "alavancagem", "leverage", "/"]) or
            _find_metrics(metrics, "Divida Liquida", exclude=["ebitda", "alavancagem", "leverage", "/"]) or
            _find_metrics(metrics, "Net Debt", exclude=["ebitda", "leverage", "/"])
        )
        dl_sorted = _fy_sorted(dl_raw)
        if dl_sorted:
            net_debt_latest_mm = round(_metric_to_mm(dl_sorted[0]), 1)
            net_debt_period_latest = dl_sorted[0].period_label
            if len(dl_sorted) >= 2:
                net_debt_prior_mm = round(_metric_to_mm(dl_sorted[1]), 1)
                net_debt_period_prior = dl_sorted[1].period_label

    net_debt_yoy_abs_mm = None
    if net_debt_latest_mm is not None and net_debt_prior_mm is not None:
        net_debt_yoy_abs_mm = round(net_debt_latest_mm - net_debt_prior_mm, 1)

    # ---- Leverage: extracted_kpis first, keyword search fallback ----
    leverage_latest_x: float | None = None
    leverage_period_latest: str | None = None
    leverage_prior_x: float | None = None
    leverage_period_prior: str | None = None
    leverage_label: str | None = None

    if ekpis and ekpis.leverage:
        leverage_latest_x = ekpis.leverage.value
        leverage_period_latest = ekpis.leverage.period
        leverage_label = ekpis.leverage.label
    else:
        lev_raw = (
            _find_metrics(metrics, "DL/EBITDA") or
            _find_metrics(metrics, "Dívida Líquida/EBITDA") or
            _find_metrics(metrics, "Divida Liquida/EBITDA") or
            _find_metrics(metrics, "Alavancagem") or
            _find_metrics(metrics, "Leverage")
        )
        lev_sorted = _fy_sorted(lev_raw)
        if lev_sorted:
            leverage_latest_x = lev_sorted[0].value
            leverage_period_latest = lev_sorted[0].period_label
            leverage_label = lev_sorted[0].label
            if len(lev_sorted) >= 2:
                leverage_prior_x = lev_sorted[1].value
                leverage_period_prior = lev_sorted[1].period_label

    return FactSheet(
        period_latest=period_latest,
        period_prior=period_prior,
        revenue_latest_mm=revenue_latest_mm,
        revenue_prior_mm=revenue_prior_mm,
        revenue_yoy_abs_mm=revenue_yoy_abs_mm,
        revenue_yoy_pct=revenue_yoy_pct,
        ebitda_latest_mm=ebitda_latest_mm,
        ebitda_prior_mm=ebitda_prior_mm,
        ebitda_period_latest=ebitda_period_latest,
        ebitda_period_prior=ebitda_period_prior,
        ebitda_margin_latest_pct=ebitda_margin_latest_pct,
        ebitda_margin_prior_pct=ebitda_margin_prior_pct,
        net_debt_latest_mm=net_debt_latest_mm,
        net_debt_prior_mm=net_debt_prior_mm,
        net_debt_period_latest=net_debt_period_latest,
        net_debt_period_prior=net_debt_period_prior,
        net_debt_yoy_abs_mm=net_debt_yoy_abs_mm,
        leverage_latest_x=leverage_latest_x,
        leverage_prior_x=leverage_prior_x,
        leverage_period_latest=leverage_period_latest,
        leverage_period_prior=leverage_period_prior,
        leverage_label=leverage_label,
    )


# ---------------------------------------------------------------------------
# Formatter — injected into agent user messages
# ---------------------------------------------------------------------------


def format_fact_sheet(fs: FactSheet) -> str:
    """Format FactSheet as a Markdown block for injection into agent prompts."""

    def _mm(v: float | None) -> str:
        return f"{v:,.1f}" if v is not None else "—"

    def _delta(v: float | None) -> str:
        if v is None:
            return "—"
        return f"+{v:,.1f}" if v > 0 else f"{v:,.1f}"

    def _pct_signed(v: float | None) -> str:
        if v is None:
            return "—"
        return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"

    def _pct(v: float | None) -> str:
        return f"{v:.1f}%" if v is not None else "—"

    def _x(v: float | None) -> str:
        return f"{v:.2f}x" if v is not None else "—"

    prior_col = fs.period_prior or "Prior"
    lev_label = fs.leverage_label or "Leverage"

    yoy = (
        f"{_delta(fs.revenue_yoy_abs_mm)} / {_pct_signed(fs.revenue_yoy_pct)}"
        if fs.revenue_yoy_abs_mm is not None
        else "—"
    )

    rows = "\n".join([
        f"| Revenue (R$ MM) | {_mm(fs.revenue_latest_mm)} | {_mm(fs.revenue_prior_mm)} | {yoy} |",
        f"| EBITDA (R$ MM) | {_mm(fs.ebitda_latest_mm)} | {_mm(fs.ebitda_prior_mm)} | — |",
        f"| EBITDA Margin [stated] | {_pct(fs.ebitda_margin_latest_pct)} | {_pct(fs.ebitda_margin_prior_pct)} | — |",
        f"| Net Debt (R$ MM) | {_mm(fs.net_debt_latest_mm)} | {_mm(fs.net_debt_prior_mm)} | {_delta(fs.net_debt_yoy_abs_mm)} |",
        f"| {lev_label} [stated] | {_x(fs.leverage_latest_x)} | {_x(fs.leverage_prior_x)} | — |",
    ])

    return f"""\
## Pre-Computed Key Metrics — use verbatim; do NOT re-derive

Reference periods: **{fs.period_latest}** vs. **{prior_col}**

| Metric | {fs.period_latest} | {prior_col} | YoY Change |
|:---|---:|---:|---:|
{rows}

Rules for using this block:
1. Revenue YoY figures are already computed from CVM account 3.01 — use them directly.
2. The leverage ratio ({lev_label}) is the company's **stated figure** from its disclosed
   metrics. Do NOT divide Net Debt by EBITDA yourself — the company uses a specific EBITDA
   basis not separately disclosed, and re-computing gives a materially different answer.
3. Metrics shown as "—" are absent from the Dossier — do not invent values for them.
"""
