"""
Credit Committee 1-Pager — 4 Purpose-Built Agents.

Each agent takes a CompanyDossier (and optional extras) and returns a
strongly-typed Pydantic object.  They reuse the same dossier context
builder as Pipeline A but emit simpler JSON — no TaggedStatements,
no citation ids — optimised for the 1-pager template shape.
"""

import json
import logging
import re

from app.analysis.committee.schemas import (
    BankContext,
    CommitteeHeaderOutput,
    CommitteeSection,
)
from app.analysis.schemas import CompanyDossier, DisclosedMetric, FinancialLineItem, QualitativeFact
from app.generation.llm import chat_completion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dossier context (lightweight — just what each agent needs)
# ---------------------------------------------------------------------------

def _period_sort_key(label: str) -> tuple:
    m = re.match(r"FY (\d{4})", label)
    if m:
        return (int(m.group(1)), 99)
    m = re.match(r"(\d+)M (\d{4})", label)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    return (0, 0)


def _fmt_value(v: float) -> str:
    return f"{v:,.0f}"


def _format_financials_brief(items: list[FinancialLineItem]) -> str:
    """Compact multi-period financial table — same layout as Pipeline A but without
    citation ids (committee agents don't cite by number)."""
    if not items:
        return "(no financial data available)"

    LABELS = {
        "DRE_con": "Income Statement (DRE)",
        "BPA_con": "Balance Sheet — Assets (BPA)",
        "BPP_con": "Balance Sheet — Liabilities & Equity (BPP)",
        "DFC_MI_con": "Cash Flow Statement (DFC)",
        "DFC_MD_con": "Cash Flow Statement (DFC)",
    }
    ORDER = list(LABELS)

    by_stmt: dict[str, list[FinancialLineItem]] = {}
    for li in items:
        by_stmt.setdefault(li.statement_type, []).append(li)

    blocks = []
    for stmt in sorted(by_stmt, key=lambda s: ORDER.index(s) if s in ORDER else 99):
        stmt_items = by_stmt[stmt]
        periods = sorted({li.period_label for li in stmt_items}, key=_period_sort_key)

        desc: dict[str, str] = {}
        vals: dict[tuple, float] = {}
        order: list[str] = []
        for li in stmt_items:
            if li.account_code not in desc:
                desc[li.account_code] = li.description
                order.append(li.account_code)
            vals[(li.account_code, li.period_label)] = li.value

        label = LABELS.get(stmt, stmt)
        header = " | ".join(periods)
        lines = [f"### {label}", "", f"| Account | Description | {header} |"]
        lines.append("|" + "---|" * (2 + len(periods)))
        for code in order:
            row = [code, desc[code][:60]]
            for p in periods:
                v = vals.get((code, p))
                row.append(_fmt_value(v) if v is not None else "—")
            lines.append("| " + " | ".join(row) + " |")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _format_metrics_brief(metrics: list[DisclosedMetric]) -> str:
    if not metrics:
        return "(no non-GAAP metrics available)"
    lines = []
    for m in metrics:
        v = f"{m.value:,.2f}" if m.value is not None else "n/a"
        u = m.unit or ""
        lines.append(f"- **{m.label}** ({m.period_label}): {v} {u}")
    return "\n".join(lines)


def _format_facts_by_section(facts: list[QualitativeFact], sections: list[str] | None = None) -> str:
    if not facts:
        return "(no qualitative facts available)"
    by_section: dict[str, list[QualitativeFact]] = {}
    for f in facts:
        if sections is None or f.section in sections:
            by_section.setdefault(f.section, []).append(f)
    if not by_section:
        return "(no relevant qualitative facts for the specified sections)"
    blocks = []
    for sec in sorted(by_section):
        sec_facts = by_section[sec]
        label = sec_facts[0].section_label
        lines = [f"**{sec} — {label}**"] + [f"- {f.text}" for f in sec_facts]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _infer_latest_year(dossier: CompanyDossier) -> str:
    if not dossier.financial_line_items:
        return "N/A"
    labels = {li.period_label for li in dossier.financial_line_items}
    best = max(labels, key=_period_sort_key)
    m = re.search(r"\d{4}", best)
    return m.group() if m else best


def _build_company_header(dossier: CompanyDossier) -> str:
    return (
        f"Company: {dossier.name} ({dossier.trade_name})\n"
        f"CNPJ: {dossier.cnpj} | Sector: {dossier.sector or 'n/a'}\n"
        f"Data coverage — DFP years: {dossier.coverage.dfp_years}; "
        f"FRE sections present: {dossier.coverage.fre_sections_present}"
    )


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _parse_json_safe(raw: str, caller: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # try to extract first {...} block
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    logger.warning("%s: could not parse LLM JSON response", caller)
    return {}


def _str_field(value: object, sep: str = " ") -> str:
    """Coerce an LLM field to str — handles lists the model sometimes returns."""
    if isinstance(value, list):
        return sep.join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


# ---------------------------------------------------------------------------
# Agent 1 — Header
# ---------------------------------------------------------------------------

_HEADER_SYSTEM = """\
You are a senior credit analyst at a major Brazilian bank, preparing the header block
of an internal credit committee 1-pager (similar to Itaú's internal format).

You receive a Company Dossier in Markdown — financial statements (DRE, BPA, BPP, DFC),
disclosed non-GAAP metrics (FRE 2.5), and qualitative facts extracted from the
Reference Form (FRE).  You may also receive Yahoo Finance market data and BCB macro context.

Your tasks:
1. Write a **framing_paragraph**: 1–2 bold sentences capturing the key credit story
   of the most recent fiscal year.  Be specific — mention revenue scale, EBITDA margin
   trend, leverage level, and the main risk or strength.
2. Assign a **grau_preocupacao** ("Baixo", "Médio", "Alto", or "Muito Alto") based on
   the company's financial health (leverage, liquidity, margin trend, refinancing risk)
   and qualitative risks (FRE 4.1 risk factors, sector dynamics, macro).
3. Write a **grau_preocupacao_reasoning**: 1–2 sentences explaining the assigned level.
4. Write **proximos_passos**: 2–3 concise action items / monitoring points for the
   credit committee (e.g. "Monitor leverage reduction trajectory; watch for debt rollover
   at end of 2026; follow up on regulatory risk flagged in FRE 4.1").
5. **extracted_ratings**: scan the qualitative FRE text for any explicit mentions of
   external credit ratings from S&P, Moody's, or Fitch for the consolidated entity
   ("cs"), the holding company ("holding"), or mature assets ("ativos_maduros").
   If found, include them.  If not found, omit the key or leave its value empty.

Language: write everything in **Portuguese**.  Keep the company's own disclosed labels
(e.g. "LAJIDA ajustado") verbatim.

Respond with ONLY valid JSON:
{
  "framing_paragraph": "...",
  "grau_preocupacao": "Baixo|Médio|Alto|Muito Alto",
  "grau_preocupacao_reasoning": "...",
  "proximos_passos": "...",
  "extracted_ratings": {"cs": "...", "holding": "...", "ativos_maduros": "..."}
}
"""


async def run_header_agent(
    dossier: CompanyDossier,
    market_ctx: str,
    bank_context: BankContext,
) -> CommitteeHeaderOutput:
    company_hdr = _build_company_header(dossier)
    financials = _format_financials_brief(dossier.financial_line_items)
    metrics = _format_metrics_brief(dossier.disclosed_metrics)
    # FRE 4.1 (risk factors) is most relevant for the header
    facts = _format_facts_by_section(dossier.qualitative_facts, sections=["4.1", "3.1", "3.2", "1.2"])
    all_facts = _format_facts_by_section(dossier.qualitative_facts)

    user_msg = f"""\
{company_hdr}

## Financial Statements
{financials}

## Disclosed Non-GAAP Metrics
{metrics}

## Qualitative Facts (FRE — key sections)
{facts}

## All Qualitative Facts (FRE)
{all_facts}
"""
    if market_ctx:
        user_msg += f"\n## Yahoo Finance Market Data\n{market_ctx}\n"

    try:
        raw = await chat_completion(_HEADER_SYSTEM, user_msg, json_mode=True)
    except Exception as exc:
        logger.error("Header agent failed: %s", exc)
        return CommitteeHeaderOutput(
            framing_paragraph="[Erro ao gerar parágrafo de enquadramento]",
            grau_preocupacao="Médio",
            grau_preocupacao_reasoning="[Erro ao gerar reasoning]",
            proximos_passos="[Erro ao gerar próximos passos]",
        )

    data = _parse_json_safe(raw, "header_agent")
    return CommitteeHeaderOutput(
        framing_paragraph=_str_field(data.get("framing_paragraph")) or "[sem conteúdo]",
        grau_preocupacao=_str_field(data.get("grau_preocupacao")) or "Médio",
        grau_preocupacao_reasoning=_str_field(data.get("grau_preocupacao_reasoning")),
        proximos_passos=_str_field(data.get("proximos_passos")),
        extracted_ratings=data.get("extracted_ratings") if isinstance(data.get("extracted_ratings"), dict) else {},
    )


# ---------------------------------------------------------------------------
# Agent 2 — Highlights Consolidado
# ---------------------------------------------------------------------------

_CONSOLIDADO_SYSTEM = """\
You are a senior credit analyst preparing the "Highlights Consolidado" section of an
internal credit committee 1-pager for a Brazilian company.

You receive the full Company Dossier (financial statements, disclosed non-GAAP metrics,
FRE qualitative facts).

Write **4 to 6 concise bullet points** describing the most recent year's consolidated
operational and financial performance.  Each bullet must be specific and quantified —
mention actual numbers (revenue growth %, EBITDA margin, net debt level, leverage ratio).
Avoid vague statements.  Focus on: revenue drivers (volume vs. price), EBITDA evolution,
Capex, financial expenses and their impact, working capital, and year-end leverage.

Write in **Portuguese**.  Keep the company's own disclosed metric labels verbatim.

Respond with ONLY valid JSON:
{
  "year_label": "2024",
  "bullets": ["...", "...", "...", "...", "..."]
}
"""


async def run_consolidado_agent(dossier: CompanyDossier) -> CommitteeSection:
    year = _infer_latest_year(dossier)
    company_hdr = _build_company_header(dossier)
    financials = _format_financials_brief(dossier.financial_line_items)
    metrics = _format_metrics_brief(dossier.disclosed_metrics)
    facts = _format_facts_by_section(dossier.qualitative_facts, sections=["2.1", "2.2", "2.5", "10.1"])

    user_msg = f"""\
{company_hdr}

## Financial Statements
{financials}

## Disclosed Non-GAAP Metrics
{metrics}

## FRE Qualitative Facts (sections 2.1, 2.2, 2.5, 10.1)
{facts}
"""

    try:
        raw = await chat_completion(_CONSOLIDADO_SYSTEM, user_msg, json_mode=True)
    except Exception as exc:
        logger.error("Consolidado agent failed: %s", exc)
        return CommitteeSection(
            section_id="highlights_consolidado",
            year_label=year,
            bullets=["[Erro ao gerar highlights consolidado]"],
        )

    data = _parse_json_safe(raw, "consolidado_agent")
    return CommitteeSection(
        section_id="highlights_consolidado",
        year_label=data.get("year_label", year),
        bullets=data.get("bullets", ["[sem conteúdo]"]),
    )


# ---------------------------------------------------------------------------
# Agent 3 — Highlights Holding
# ---------------------------------------------------------------------------

_HOLDING_SYSTEM = """\
You are a senior credit analyst preparing the "Highlights Holding" section of an
internal credit committee 1-pager for a Brazilian company.

You receive the Company Dossier (balance sheet, FRE governance/ownership sections).

Your task: write **3 to 4 bullet points** on the holding-company view — dividend upstream
flows (dividends paid by subsidiaries to the holding), support provided to subsidiaries,
intercompany dynamics, and holding-level leverage separate from the consolidated view.

IMPORTANT: if the company has a single legal entity with no holding structure, write
exactly one bullet: "Not applicable — company has a single-entity structure with no
holding layer."

Write in **Portuguese**.  Keep the company's own disclosed metric labels verbatim.

Respond with ONLY valid JSON:
{
  "year_label": "2024",
  "bullets": ["...", "...", "..."]
}
"""


async def run_holding_agent(dossier: CompanyDossier) -> CommitteeSection:
    year = _infer_latest_year(dossier)
    company_hdr = _build_company_header(dossier)
    financials = _format_financials_brief(dossier.financial_line_items)
    facts = _format_facts_by_section(dossier.qualitative_facts, sections=["6.5", "7.1", "1.4", "1.5", "6.1"])

    user_msg = f"""\
{company_hdr}

## Balance Sheet (assets and liabilities)
{financials}

## FRE Qualitative Facts (governance, ownership, group structure)
{facts}
"""

    try:
        raw = await chat_completion(_HOLDING_SYSTEM, user_msg, json_mode=True)
    except Exception as exc:
        logger.error("Holding agent failed: %s", exc)
        return CommitteeSection(
            section_id="highlights_holding",
            year_label=year,
            bullets=["[Erro ao gerar highlights holding]"],
        )

    data = _parse_json_safe(raw, "holding_agent")
    return CommitteeSection(
        section_id="highlights_holding",
        year_label=data.get("year_label", year),
        bullets=data.get("bullets", ["[sem conteúdo]"]),
    )


# ---------------------------------------------------------------------------
# Agent 4 — Perspectivas
# ---------------------------------------------------------------------------

_PERSPECTIVAS_SYSTEM = """\
You are a senior credit analyst preparing the "Perspectivas" (Outlook) section of an
internal credit committee 1-pager for a Brazilian company.

You receive the Company Dossier (FRE forward-looking sections, risk factors) and
optionally Yahoo Finance market data.

Write **3 to 4 forward-looking bullet points** covering: deleveraging trajectory
(when leverage is expected to drop), new concessions or M&A plans mentioned by
management, asset sale programmes, debt maturity / rollover schedule, and any
macro or sector tailwinds/headwinds relevant to the company.

Be specific and grounded in evidence from the FRE.  Do not invent numbers.
If the company mentions a specific target leverage or debt reduction plan, quote it.

Write in **Portuguese**.  Keep the company's own disclosed metric labels verbatim.

Respond with ONLY valid JSON:
{
  "year_label": "2025",
  "bullets": ["...", "...", "..."]
}
"""


async def run_perspectivas_agent(dossier: CompanyDossier, market_ctx: str) -> CommitteeSection:
    year = _infer_latest_year(dossier)
    try:
        next_year = str(int(year) + 1)
    except ValueError:
        next_year = year

    company_hdr = _build_company_header(dossier)
    facts = _format_facts_by_section(dossier.qualitative_facts, sections=["4.1", "3.1", "3.2", "3.3", "3.4"])
    # Supplement with any remaining forward-looking / risk facts
    all_facts = _format_facts_by_section(dossier.qualitative_facts)

    user_msg = f"""\
{company_hdr}

## FRE Forward-Looking & Risk Sections (4.1, 3.x)
{facts}

## All Qualitative Facts (FRE)
{all_facts}
"""
    if market_ctx:
        user_msg += f"\n## Yahoo Finance Market Data\n{market_ctx}\n"

    try:
        raw = await chat_completion(_PERSPECTIVAS_SYSTEM, user_msg, json_mode=True)
    except Exception as exc:
        logger.error("Perspectivas agent failed: %s", exc)
        return CommitteeSection(
            section_id="perspectivas",
            year_label=next_year,
            bullets=["[Erro ao gerar perspectivas]"],
        )

    data = _parse_json_safe(raw, "perspectivas_agent")
    return CommitteeSection(
        section_id="perspectivas",
        year_label=data.get("year_label", next_year),
        bullets=data.get("bullets", ["[sem conteúdo]"]),
    )
