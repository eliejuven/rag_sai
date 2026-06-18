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

from app.analysis.committee.fact_sheet import FactSheet, format_fact_sheet
from app.analysis.committee.schemas import (
    BankContext,
    CitedBullet,
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


def _fmt_mm(v: float) -> str:
    """Format a raw-R$ value as R$ MM with one decimal place."""
    return f"{v / 1_000_000:,.1f}"


def _format_financials_brief(items: list[FinancialLineItem]) -> str:
    """Compact multi-period financial table — values in R$ MM.

    FinancialLineItem.value is always in raw R$ (scale multiplier already applied
    by cvm_client), so dividing by 1 000 000 converts to R$ MM.
    """
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
        lines = [f"### {label} (R$ MM)", "", f"| Account | Description | {header} |"]
        lines.append("|" + "---|" * (2 + len(periods)))
        for code in order:
            row = [code, desc[code][:60]]
            for p in periods:
                v = vals.get((code, p))
                row.append(_fmt_mm(v) if v is not None else "—")
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


def _parse_cited_bullets(raw: list, fallback: str = "[sem conteúdo]") -> list[CitedBullet]:
    """Parse LLM bullets — accepts both {"text":..,"source":..} dicts and plain strings."""
    if not raw:
        return [CitedBullet(text=fallback)]
    result = []
    for item in raw:
        if isinstance(item, dict):
            result.append(CitedBullet(
                text=_str_field(item.get("text", "")),
                source=_str_field(item.get("source", "")),
            ))
        elif isinstance(item, str):
            result.append(CitedBullet(text=item))
    return result or [CitedBullet(text=fallback)]


# ---------------------------------------------------------------------------
# Agent 1 — Header
# ---------------------------------------------------------------------------

_GROUNDING_RULES = """\
GROUNDING RULES (mandatory — violations invalidate the output):
- Every fact and number you write MUST appear verbatim or be directly calculable from
  values explicitly stated in the Dossier provided below.
- Do NOT compute derived metrics (YoY growth %, margins, ratios, multipliers) unless
  the exact figure is explicitly stated in the Dossier (e.g. in FRE 2.5 disclosed metrics
  or in the financial statements with both operands present).  If you would need to divide
  or subtract to produce a number, do NOT include it — write 'informação não disponível'.
- Do NOT draw on your training-data knowledge about this company. Use ONLY the
  information present in the Dossier context below.  If a fact is not in the Dossier,
  omit it or write 'informação não disponível'.
- Never fabricate ratings, targets, guidance figures, or events not mentioned in the Dossier.
- MONETARY FORMAT:
  • Values in the "Financial Statements (R$ MM)" table are pre-converted — cite them exactly
    as shown (e.g. "R$ 11,096.2 MM"). Do NOT write "R$ X mil" or "R$ X thousand" for those.
  • For amounts from FRE qualitative text (sections 2.1, 2.10, 4.1, etc.): CVM filings report
    these in R$ thousand (mil). Express them in rounded bilhões (divide by 1,000,000).
    Examples: "R$20.453.194" (no unit) → "~R$20,5 bilhões"; "R$39.021.033 mil" → "~R$39,0 bilhões".
  • FORBIDDEN: writing a raw thousands-scale number followed by "MM"
    (e.g. NEVER write "R$ 39.021.033 MM" or "R$ 20.453.194 MM").
"""

_HEADER_SYSTEM = """\
You are a senior credit analyst at a major Brazilian bank, preparing the header block
of an internal credit committee 1-pager (similar to Itaú's internal format).

You receive a Company Dossier in Markdown — financial statements (DRE, BPA, BPP, DFC),
disclosed non-GAAP metrics (FRE 2.5), and qualitative facts extracted from the
Reference Form (FRE).  You may also receive Yahoo Finance market data and BCB macro context.

""" + _GROUNDING_RULES + """
If the user message begins with a "Pre-Computed Key Metrics" block, those figures have
been derived correctly from the raw CVM data — use them verbatim for revenue, EBITDA,
margin, net debt, and leverage.  Do not re-derive them from the financial statements.

Your tasks:
1. Write a **framing_paragraph**: 1–2 bold sentences capturing the key credit story
   of the most recent fiscal year.  Cite only numbers that are explicitly in the Dossier
   (e.g. the disclosed revenue figure, a stated EBITDA margin, a disclosed leverage ratio).
   If a metric is not stated in the Dossier, describe the trend qualitatively instead.
2. Assign a **grau_preocupacao** ("Baixo", "Médio", "Alto", or "Muito Alto") based on
   the company's financial health (leverage, liquidity, margin trend, refinancing risk)
   and qualitative risks (FRE 4.1 risk factors, sector dynamics, macro).
3. Write a **grau_preocupacao_reasoning**: 1–2 sentences explaining the assigned level,
   citing only figures present in the Dossier.
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
    fact_sheet: FactSheet | None = None,
) -> CommitteeHeaderOutput:
    company_hdr = _build_company_header(dossier)
    financials = _format_financials_brief(dossier.financial_line_items)
    metrics = _format_metrics_brief(dossier.disclosed_metrics)
    # FRE 4.1 (risk factors) is most relevant for the header
    facts = _format_facts_by_section(dossier.qualitative_facts, sections=["4.1", "3.1", "3.2", "1.2"])
    all_facts = _format_facts_by_section(dossier.qualitative_facts)

    user_msg = ""
    if fact_sheet is not None:
        user_msg += format_fact_sheet(fact_sheet) + "\n"

    user_msg += f"""\
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

def _citation_format(period: str) -> str:
    """Return the citation format instruction block with the actual current period."""
    return f"""\
Source format for each bullet (use the most specific available):
- Financial statement line: "Conta X.XX — [statement] — [período]"
  e.g. "Conta 3.01 — DRE_con — {period}"
- Disclosed non-GAAP metric: "Métrica divulgada — [label] — [período]"
  e.g. "Métrica divulgada — EBITDA Ajustado — {period}"
- FRE qualitative section: "FRE [section] — [section_label]"
  e.g. "FRE 2.1 — Resultados Operacionais"
- If you cannot identify a specific source, use an empty string "" — do NOT
  invent a source.
IMPORTANT: The current reporting period is **{period}**. Use this exact label when
citing current-year data. Do not write the prior-year period when the cited value
comes from the current year.
"""

def _consolidado_system(period: str) -> str:
    year = re.search(r"\d{4}", period)
    year_str = year.group() if year else period
    return f"""\
You are a senior credit analyst preparing the "Highlights Consolidado" section of an
internal credit committee 1-pager for a Brazilian company.

You receive the full Company Dossier (financial statements, disclosed non-GAAP metrics,
FRE qualitative facts).

{_GROUNDING_RULES}
If the user message begins with a "Pre-Computed Key Metrics" block, those figures have
been derived correctly from the raw CVM data — use them verbatim for revenue, EBITDA,
margin, net debt, and leverage.  Do not re-derive them from the financial statements.

Write **4 to 6 concise bullet points** describing the most recent year's consolidated
operational and financial performance.  Only cite numbers that appear explicitly in the
Dossier (account values from the financial statements, or figures from the disclosed
non-GAAP metrics).  Do NOT compute growth rates, margins, or ratios yourself — if a
percentage or ratio is not stated in the Dossier, describe the trend qualitatively
(e.g. "receita cresceu no período" instead of "receita cresceu 12%").
Focus on: revenue drivers (volume vs. price), EBITDA evolution, Capex, financial
expenses and their impact, working capital, and year-end leverage.

For each bullet, identify the specific source in the Dossier.
{_citation_format(period)}
Write in **Portuguese**.  Keep the company's own disclosed metric labels verbatim.

Respond with ONLY valid JSON:
{{
  "year_label": "{year_str}",
  "bullets": [
    {{"text": "...", "source": "Conta 3.01 — DRE_con — {period}"}},
    {{"text": "...", "source": "Métrica divulgada — EBITDA Ajustado — {period}"}}
  ]
}}
"""


async def run_consolidado_agent(
    dossier: CompanyDossier,
    fact_sheet: FactSheet | None = None,
) -> CommitteeSection:
    year = _infer_latest_year(dossier)
    period = fact_sheet.period_latest if fact_sheet else f"FY {year}"
    company_hdr = _build_company_header(dossier)
    financials = _format_financials_brief(dossier.financial_line_items)
    metrics = _format_metrics_brief(dossier.disclosed_metrics)
    facts = _format_facts_by_section(dossier.qualitative_facts, sections=["2.1", "2.2", "2.5", "10.1"])

    user_msg = ""
    if fact_sheet is not None:
        user_msg += format_fact_sheet(fact_sheet) + "\n"

    user_msg += f"""\
{company_hdr}

## Financial Statements (values in R$ MM)
{financials}

## Disclosed Non-GAAP Metrics
{metrics}

## FRE Qualitative Facts (sections 2.1, 2.2, 2.5, 10.1)
{facts}
"""

    try:
        raw = await chat_completion(_consolidado_system(period), user_msg, json_mode=True)
    except Exception as exc:
        logger.error("Consolidado agent failed: %s", exc)
        return CommitteeSection(
            section_id="highlights_consolidado",
            year_label=year,
            bullets=[CitedBullet(text="[Erro ao gerar highlights consolidado]")],
        )

    data = _parse_json_safe(raw, "consolidado_agent")
    return CommitteeSection(
        section_id="highlights_consolidado",
        year_label=data.get("year_label", year),
        bullets=_parse_cited_bullets(data.get("bullets", []), fallback="[sem conteúdo]"),
    )


# ---------------------------------------------------------------------------
# Agent 3 — Highlights Holding
# ---------------------------------------------------------------------------

def _holding_system(period: str) -> str:
    year = re.search(r"\d{4}", period)
    year_str = year.group() if year else period
    return f"""\
You are a senior credit analyst preparing the "Highlights Holding" section of an
internal credit committee 1-pager for a Brazilian company.

You receive the Company Dossier (balance sheet, FRE governance/ownership sections).

{_GROUNDING_RULES}
Your task: write **3 to 4 bullet points** on the holding-company view — dividend upstream
flows (dividends received from subsidiaries, DFC account 6.02.05), support provided to
subsidiaries, intercompany dynamics, and holding-level leverage separate from the
consolidated view.  Only cite dividend amounts, intercompany balances, or leverage figures
that are explicitly stated in the Dossier.  If the amount is not stated, describe the
relationship qualitatively.

CRITICAL — mutually exclusive logic:
- If the Dossier contains ANY mention of subsidiaries, intercompany transactions, or
  non-zero "Dividendos recebidos" (DFC 6.02.05), write the full 3–4 dividend/holding
  bullets and do NOT include the "não aplicável" bullet.
- Only write the single "não aplicável" bullet when the Dossier shows ZERO subsidiaries,
  zero intercompany flows, and no DFC dividends received entries.
- Never mix holding bullets with the "não aplicável" bullet — they contradict each other.

For each bullet, identify the specific source in the Dossier.
{_citation_format(period)}
Write in **Portuguese**.  Keep the company's own disclosed metric labels verbatim.

Respond with ONLY valid JSON:
{{
  "year_label": "{year_str}",
  "bullets": [
    {{"text": "...", "source": "Conta 6.02.05 — DFC_con — {period}"}},
    {{"text": "...", "source": "FRE 1.2 — Grupo Econômico"}}
  ]
}}
"""


async def run_holding_agent(dossier: CompanyDossier) -> CommitteeSection:
    year = _infer_latest_year(dossier)
    period = f"FY {year}"
    company_hdr = _build_company_header(dossier)
    financials = _format_financials_brief(dossier.financial_line_items)
    facts = _format_facts_by_section(dossier.qualitative_facts, sections=["6.5", "7.1", "1.4", "1.5", "6.1"])

    user_msg = f"""\
{company_hdr}

## Balance Sheet (assets and liabilities, values in R$ MM)
{financials}

## FRE Qualitative Facts (governance, ownership, group structure)
{facts}
"""

    try:
        raw = await chat_completion(_holding_system(period), user_msg, json_mode=True)
    except Exception as exc:
        logger.error("Holding agent failed: %s", exc)
        return CommitteeSection(
            section_id="highlights_holding",
            year_label=year,
            bullets=[CitedBullet(text="[Erro ao gerar highlights holding]")],
        )

    data = _parse_json_safe(raw, "holding_agent")
    return CommitteeSection(
        section_id="highlights_holding",
        year_label=data.get("year_label", year),
        bullets=_parse_cited_bullets(data.get("bullets", []), fallback="[sem conteúdo]"),
    )


# ---------------------------------------------------------------------------
# Agent 4 — Perspectivas
# ---------------------------------------------------------------------------

def _perspectivas_system(period: str) -> str:
    year = re.search(r"\d{4}", period)
    next_year_str = str(int(year.group()) + 1) if year else period
    return f"""\
You are a senior credit analyst preparing the "Perspectivas" (Outlook) section of an
internal credit committee 1-pager for a Brazilian company.

You receive the Company Dossier (FRE forward-looking sections, risk factors) and
optionally Yahoo Finance market data.

{_GROUNDING_RULES}
Write **3 to 4 forward-looking bullet points** covering: deleveraging trajectory,
new concessions or M&A plans mentioned by management, asset sale programmes, debt
maturity / rollover schedule, and macro or sector tailwinds/headwinds.

Strict rules:
- Only include guidance targets, leverage goals, or debt schedules that are explicitly
  stated in the FRE text.  If management did not disclose a specific target, do NOT
  invent one.
- Macro / sector context (from Yahoo Finance market data or BCB data) may be used to
  describe tailwinds/headwinds qualitatively, but do not attribute specific macro numbers
  (GDP growth %, Selic rate) to the company's own projections.
- Quote management language verbatim when the FRE uses it.

For each bullet, identify the specific source in the Dossier.
{_citation_format(period)}
Write in **Portuguese**.  Keep the company's own disclosed metric labels verbatim.

Respond with ONLY valid JSON:
{{
  "year_label": "{next_year_str}",
  "bullets": [
    {{"text": "...", "source": "FRE 3.1 — Plano de Negócios"}},
    {{"text": "...", "source": "FRE 4.1 — Fatores de Risco"}}
  ]
}}
"""


async def run_perspectivas_agent(dossier: CompanyDossier, market_ctx: str) -> CommitteeSection:
    year = _infer_latest_year(dossier)
    period = f"FY {year}"
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
        next_year = str(int(year) + 1) if year.isdigit() else year
    except ValueError:
        next_year = year

    try:
        raw = await chat_completion(_perspectivas_system(period), user_msg, json_mode=True)
    except Exception as exc:
        logger.error("Perspectivas agent failed: %s", exc)
        return CommitteeSection(
            section_id="perspectivas",
            year_label=next_year,
            bullets=[CitedBullet(text="[Erro ao gerar perspectivas]")],
        )

    data = _parse_json_safe(raw, "perspectivas_agent")
    return CommitteeSection(
        section_id="perspectivas",
        year_label=data.get("year_label", next_year),
        bullets=_parse_cited_bullets(data.get("bullets", []), fallback="[sem conteúdo]"),
    )
