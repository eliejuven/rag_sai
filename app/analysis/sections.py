"""
Phase 4 — Tagged section generators ("agents").

Each generate_<section_id>() function is one agent from the v1 roster (see
TODO.md Phase 4.2): it receives the FULL CompanyDossier (Decision 1) plus the
sector playbook (Phase 3) and returns one SectionOutput — a list of
TaggedStatements (fact / inference / judgment), each "fact" citing back into
the Dossier.

Citations are resolved through a per-call _EvidenceIndex: _build_dossier_context()
renders the Dossier as Markdown with inline [N] source markers — deduplicated,
since many financial line items / qualitative facts share one source page —
and the LLM is asked to pick existing [N]s rather than invent citations.
_EvidenceIndex maps those ids back to real Citation objects.

generate_all_sections() runs the full roster in the order required by
Decision 6: agents 1-7 (any order), then MIT Outlook (8, sees 1-7 outputs),
then Limitations & Coverage (9, sees 1-8 outputs).
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from app import storage
from app.analysis.schemas import (
    Citation,
    CompanyDossier,
    DisclosedMetric,
    FinancialLineItem,
    QualitativeFact,
    SectionOutput,
    TaggedStatement,
)
from app.embeddings.client import embed_texts
from app.generation.llm import chat_completion
from app.search.keyword_search import bm25_index
from app.search.reranker import reciprocal_rank_fusion
from app.search.vector_store import vector_store

logger = logging.getLogger(__name__)

_MAX_DESC_LEN = 60

# Cap concurrent LLM calls. Phase 4 prompts are much larger than Phase 1's
# per-FRE-section extraction calls (~20k tokens vs ~3k) — even concurrency=3
# blows through Mistral's per-minute token quota and 429s despite retry/backoff,
# so for now this runs sequentially.
_MAX_CONCURRENT_SECTIONS = 1

# Even sequential calls can land close enough together to trip the per-minute
# token quota with these larger prompts — a short pause between agents spreads
# them out and noticeably reduces 429s.
_INTER_SECTION_DELAY_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Evidence index — dedupes Citations dossier-wide so the LLM can reference
# sources by a short numeric id instead of inventing/repeating Citation JSON.
# ---------------------------------------------------------------------------


def _citation_key(c: Citation) -> tuple:
    return (c.document_id, c.section, c.page_number)


class _EvidenceIndex:
    """Dedupes Citations; the LLM references them by small int ids ("[N]")."""

    def __init__(self):
        self._citations: list[Citation] = []
        self._by_key: dict[tuple, int] = {}

    def id_for(self, citation: Citation) -> int:
        key = _citation_key(citation)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        self._citations.append(citation)
        new_id = len(self._citations)
        self._by_key[key] = new_id
        return new_id

    def resolve(self, ids: list) -> list[Citation]:
        resolved = []
        for i in ids:
            if isinstance(i, int) and 1 <= i <= len(self._citations):
                resolved.append(self._citations[i - 1])
        return resolved


# ---------------------------------------------------------------------------
# Dossier -> Markdown context
# ---------------------------------------------------------------------------

STATEMENT_LABELS = {
    "DRE_con": "Income Statement (DRE)",
    "BPA_con": "Balance Sheet - Assets (BPA)",
    "BPP_con": "Balance Sheet - Liabilities & Equity (BPP)",
    "DFC_MI_con": "Cash Flow Statement - Indirect Method (DFC)",
    "DFC_MD_con": "Cash Flow Statement - Direct Method (DFC)",
}

_STATEMENT_ORDER = list(STATEMENT_LABELS)


def _period_sort_key(period_label: str) -> tuple[int, int]:
    """Sort "FY 2024" / "3M 2025 (Jan–Mar)" chronologically (FY last in its year)."""
    m = re.match(r"FY (\d{4})", period_label)
    if m:
        return (int(m.group(1)), 99)
    m = re.match(r"(\d+)M (\d{4})", period_label)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    return (0, 0)


def _fmt_value(value: float) -> str:
    return f"{value:,.0f}"


def _truncate(text: str, max_len: int = _MAX_DESC_LEN) -> str:
    text = text.strip().replace("|", "/")
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _format_financial_statements(
    line_items: list[FinancialLineItem], evidence: _EvidenceIndex
) -> str:
    """Pivot line items into one Markdown table per statement type, accounts
    as rows and periods as columns — far more compact than one row per
    (account, period), and lets the LLM read trends across periods directly."""
    if not line_items:
        return "(no structured financial data available)"

    by_statement: dict[str, list[FinancialLineItem]] = {}
    for li in line_items:
        by_statement.setdefault(li.statement_type, []).append(li)

    blocks = []
    for statement_type in sorted(
        by_statement,
        key=lambda s: _STATEMENT_ORDER.index(s) if s in _STATEMENT_ORDER else 99,
    ):
        items = by_statement[statement_type]
        periods = sorted({li.period_label for li in items}, key=_period_sort_key)

        descriptions: dict[str, str] = {}
        values: dict[tuple[str, str], float] = {}
        period_citation_id: dict[str, int] = {}
        order: list[str] = []
        for li in items:
            if li.account_code not in descriptions:
                descriptions[li.account_code] = li.description
                order.append(li.account_code)
            values[(li.account_code, li.period_label)] = li.value
            period_citation_id.setdefault(li.period_label, evidence.id_for(li.citation))

        label = STATEMENT_LABELS.get(statement_type, statement_type)
        header_cols = " | ".join(f"{p} [{period_citation_id[p]}]" for p in periods)
        lines = [f"### {label}", "", f"| Account | Description | {header_cols} |"]
        lines.append("|" + "---|" * (2 + len(periods)))
        for code in order:
            row = [code, _truncate(descriptions[code])]
            for p in periods:
                v = values.get((code, p))
                row.append(_fmt_value(v) if v is not None else "—")
            lines.append("| " + " | ".join(row) + " |")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _format_disclosed_metrics(metrics: list[DisclosedMetric], evidence: _EvidenceIndex) -> str:
    if not metrics:
        return "(no non-GAAP metrics extracted)"
    lines = []
    for m in metrics:
        cid = evidence.id_for(m.citation)
        value = f"{m.value:,.2f}" if m.value is not None else "n/a"
        unit = m.unit or ""
        definition = f" - definition: {m.definition}" if m.definition else ""
        lines.append(f"- **{m.label}** ({m.period_label}): {value} {unit}{definition} [{cid}]")
    return "\n".join(lines)


def _format_qualitative_facts(facts: list[QualitativeFact], evidence: _EvidenceIndex) -> str:
    if not facts:
        return "(no qualitative facts extracted)"
    by_section: dict[str, list[QualitativeFact]] = {}
    for f in facts:
        by_section.setdefault(f.section, []).append(f)

    blocks = []
    for section in sorted(by_section):
        section_facts = by_section[section]
        label = section_facts[0].section_label
        lines = [f"**{section} — {label}**"]
        for f in section_facts:
            cid = evidence.id_for(f.citation)
            lines.append(f"- {f.text} [{cid}]")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_conflicts(dossier: CompanyDossier) -> str:
    if not dossier.conflicts:
        return "(no data conflicts identified)"
    return "\n".join(f"- {c.description}" for c in dossier.conflicts)


def _format_coverage(dossier: CompanyDossier) -> str:
    cov = dossier.coverage
    return (
        f"- Years with DFP (annual): {cov.dfp_years or 'none'}\n"
        f"- Years with ITR (quarterly): {cov.itr_years or 'none'}\n"
        f"- Years with FRE: {cov.fre_years or 'none'}\n"
        f"- FRE sections present: {', '.join(cov.fre_sections_present) or 'none'}\n"
        f"- FRE sections missing: {', '.join(cov.fre_sections_missing) or 'none'}"
    )


def _build_dossier_context(dossier: CompanyDossier) -> tuple[str, _EvidenceIndex]:
    """Render the full Dossier as Markdown with inline [N] source markers."""
    evidence = _EvidenceIndex()

    header = (
        f"# Company Dossier - {dossier.name} ({dossier.trade_name})\n"
        f"CNPJ: {dossier.cnpj} | CD_CVM: {dossier.cd_cvm} | Sector: {dossier.sector or 'n/a'}\n"
        f"Generated on: {dossier.generated_at.isoformat()}\n\n"
        f"## Data Coverage\n{_format_coverage(dossier)}"
    )

    financials = _format_financial_statements(dossier.financial_line_items, evidence)
    metrics = _format_disclosed_metrics(dossier.disclosed_metrics, evidence)
    facts = _format_qualitative_facts(dossier.qualitative_facts, evidence)
    conflicts = _format_conflicts(dossier)

    context = (
        f"{header}\n\n"
        f"## Financial Statements (DFP/ITR)\n\n{financials}\n\n"
        f"## Disclosed Non-GAAP Metrics (FRE 2.5)\n\n{metrics}\n\n"
        f"## Qualitative Facts (FRE)\n\n{facts}\n\n"
        f"## Data Conflicts\n\n{conflicts}"
    )
    return context, evidence


# ---------------------------------------------------------------------------
# Fallback search (Decision 2) — plain function call into the existing search
# primitives when the Dossier is thin for an agent's topic. Not an LLM
# tool-use loop; any fact sourced this way still gets a Citation.
# ---------------------------------------------------------------------------


async def _fallback_search(
    dossier: CompanyDossier, query: str, top_k: int = 3
) -> list[tuple[Citation, str]]:
    """A few extra chunks for `query`, restricted to this company's documents."""
    if not storage.chunks:
        return []

    try:
        vectors = await embed_texts([query])
    except Exception as e:
        logger.warning("Fallback search embedding failed: %s", e)
        return []

    vec_results = vector_store.search(vectors[0], top_k=top_k * 5)
    bm25_results = bm25_index.search(query, top_k=top_k * 5)
    fused = reciprocal_rank_fusion(vec_results, bm25_results, top_k=top_k * 5)

    results = []
    for chunk_index, _score in fused:
        chunk = storage.chunks[chunk_index]
        if chunk.get("cnpj") != dossier.cnpj:
            continue
        citation = Citation(
            document_id=chunk["document_id"],
            filename=chunk["filename"],
            section=chunk.get("section"),
            section_label=chunk.get("section_label"),
            page_number=chunk.get("page_number"),
        )
        results.append((citation, chunk["text"]))
        if len(results) >= top_k:
            break
    return results


def _format_fallback_evidence(extra: list[tuple[Citation, str]], evidence: _EvidenceIndex) -> str:
    lines = ["\n\n## Additional Evidence (supplementary search)\n"]
    for citation, text in extra:
        cid = evidence.id_for(citation)
        snippet = text.strip().replace("\n", " ")
        if len(snippet) > 500:
            snippet = snippet[:499] + "…"
        label = f"{citation.section} — {citation.section_label}" if citation.section else citation.filename
        lines.append(f"- **{label}** [{cid}]: {snippet}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent roster (Phase 4.2 — v1)
# ---------------------------------------------------------------------------


@dataclass
class AgentDef:
    section_id: str
    title: str
    role: str
    task: str
    focus_fre_sections: list[str] = field(default_factory=list)
    uses_prior_sections: bool = False


_SYSTEM_TEMPLATE = """You are a senior credit analyst, acting as {role}, analyzing a publicly listed Brazilian company registered with the CVM (Brazilian Securities Commission).

You receive a "Company Dossier" in Markdown — financial statements (DFP/ITR, multiple periods), non-GAAP metrics, and qualitative facts extracted from the Reference Form (Formulário de Referência / FRE). Each relevant data point ends with a source identifier in brackets, e.g. [12] — these identifiers reference real citations already present in the Dossier.{extra}

Your task: {task}

Rules:
- "fact": a statement extracted directly from the Dossier. "citation_ids" must contain at least one identifier EXACTLY as it appears in the Dossier (never invent a number).
- "inference": a conclusion derived by reasoning/calculation from facts in the Dossier. Fill "derived_from" with a brief summary of the facts combined; include "citation_ids" of the underlying facts whenever possible.
- "judgment": an analytical judgment grounded in the Sector Playbook. Fill "basis" indicating which section of the playbook informed the judgment (e.g. "Sector Playbook §1").
- NEVER write bracket-style references (e.g. "[25]", "[2.03.01]", "[Sector Playbook §1]") inside "text", "derived_from", or "basis" — these fields must be plain prose, with no brackets. Source identifiers (the bracketed numbers from the Dossier) go ONLY in "citation_ids"; the Sector Playbook section goes only in "basis", as plain text (e.g. "Sector Playbook §1"), with no brackets.
- For metrics such as EBITDA (adjusted or not), net debt, and similar: if the company already discloses this value in "Disclosed Non-GAAP Metrics (FRE 2.5)", use EXACTLY that value (with the company's own definition) as a "fact" — NEVER calculate your own version from the DRE/BPA/BPP, as this creates numbers that conflict with other sections. Only calculate ("inference") a metric of this kind if it is absent from the disclosed metrics.
- Prioritize the trend across the periods available in the Dossier, not just the most recent value.
- When relevant data is missing, say so explicitly rather than inventing it.
- The company's own disclosed terms/labels (e.g. "LAJIDA ajustado") may be kept verbatim even if in Portuguese, but the rest of the text must be written in English.
- Produce between 3 and 10 statements. Respond in English.

Respond with ONLY valid JSON, in this exact format:
{{"statements": [{{"type": "fact|inference|judgment", "text": "...", "citation_ids": [1,2], "derived_from": ["..."] or null, "basis": "..." or null}}]}}"""

_PRIOR_SECTIONS_HINT = (
    " You also receive the sections already produced by the other agents — use them as"
    " additional context, but don't repeat what they already said."
)


AGENT_ROSTER: list[AgentDef] = [
    AgentDef(
        section_id="business_segments",
        title="Business & Segments",
        role="the Business & Segments agent",
        task=(
            "describe the company's identity, its main businesses and operating segments, "
            "and how they generate revenue — based on the Dossier (especially "
            "FRE 1.2, 1.3, 1.6, 2.10)."
        ),
        focus_fre_sections=["1.2", "1.3", "1.6", "2.10"],
    ),
    AgentDef(
        section_id="financial_performance",
        title="Financial Performance",
        role="the Financial Performance agent",
        task=(
            "analyze revenue, margins, and net result across the periods available in the "
            "income statement (DRE), highlighting trends, and relate them to management's "
            "discussion (FRE 2.1, 2.2) when relevant."
        ),
        focus_fre_sections=["2.1", "2.2"],
    ),
    AgentDef(
        section_id="debt_capital_structure",
        title="Debt & Capital Structure",
        role="the Debt & Capital Structure agent",
        task=(
            "analyze the level and trajectory of indebtedness, the mix of short- vs. "
            "long-term debt, and shareholders' equity (BPA/BPP), cross-referencing relevant "
            "contracts, covenants, and financial conditions described in the FRE (1.15, 2.1)."
        ),
        focus_fre_sections=["1.15", "2.1"],
    ),
    AgentDef(
        section_id="cash_flow_liquidity",
        title="Cash Flow & Liquidity",
        role="the Cash Flow & Liquidity agent",
        task=(
            "analyze operating, investing, and financing cash flows (DFC) across the "
            "available periods, and assess the company's liquidity position."
        ),
    ),
    AgentDef(
        section_id="risk_contingencies",
        title="Risk Factors & Contingencies",
        role="the Risk Factors & Contingencies agent",
        task=(
            "identify the main disclosed risk factors and contingencies (FRE 4.1, "
            "4.2, 4.3, 4.7, 5.1, 2.8) and, using the Sector Playbook, judge which of these "
            "risks are most relevant to this company's credit analysis specifically."
        ),
        focus_fre_sections=["4.1", "4.2", "4.3", "4.7", "5.1", "2.8"],
    ),
    AgentDef(
        section_id="non_gaap_kpis",
        title="Non-GAAP Metrics & KPIs",
        role="the Non-GAAP Metrics & KPIs agent",
        task=(
            "present the non-GAAP metrics disclosed by the company (FRE 2.5), preserving "
            "their exact names and definitions, and — where possible — relate them to the "
            "corresponding DRE/DFC lines for context."
        ),
        focus_fre_sections=["2.5"],
    ),
    AgentDef(
        section_id="governance_ownership",
        title="Governance & Ownership Structure",
        role="the Governance & Ownership Structure agent",
        task=(
            "describe the ownership structure, the economic group, the integrity program, "
            "and the composition of management bodies (FRE 1.12, 5.3, 6.5, 7.1), highlighting "
            "anything relevant to credit risk (e.g., related-party transactions, "
            "concentration of control)."
        ),
        focus_fre_sections=["1.12", "5.3", "6.5", "7.1"],
    ),
    AgentDef(
        section_id="mit_outlook",
        title="Outlook (MIT Outlook)",
        role="the Outlook agent — the 'MIT lens' of senior analytical judgment",
        task=(
            "based on the Dossier and the sections already generated by the other agents, "
            "formulate the company's credit outlook — the analytical opinion that ties the "
            "prior facts and inferences together into a coherent judgment, explicitly "
            "following the Sector Playbook (especially section 5, 'characteristic reasoning')."
        ),
        uses_prior_sections=True,
    ),
    AgentDef(
        section_id="limitations_coverage",
        title="Limitations & Coverage",
        role="the Limitations & Coverage agent (meta-analysis)",
        task=(
            "based on the Dossier's data coverage (missing FRE sections, available years), "
            "the data conflicts identified, and the sections already generated, explicitly "
            "list this analysis's limitations — what could not be assessed due to missing "
            "data, and any unresolved conflicts."
        ),
        uses_prior_sections=True,
    ),
]

_AGENTS_BY_ID = {a.section_id: a for a in AGENT_ROSTER}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _build_system_prompt(agent: AgentDef) -> str:
    extra = _PRIOR_SECTIONS_HINT if agent.uses_prior_sections else ""
    return _SYSTEM_TEMPLATE.format(role=agent.role, task=agent.task, extra=extra)


def _format_prior_sections(prior_sections: list[SectionOutput]) -> str:
    if not prior_sections:
        return ""
    blocks = []
    for s in prior_sections:
        lines = [f"### {s.title}"]
        for st in s.statements:
            lines.append(f"- [{st.type}] {st.text}")
        blocks.append("\n".join(lines))
    return "\n\n## Previously Generated Sections\n\n" + "\n\n".join(blocks)


def _build_user_message(
    dossier: CompanyDossier,
    playbook: str,
    context: str,
    agent: AgentDef,
    prior_sections: list[SectionOutput] | None,
) -> str:
    parts = [context, f"\n\n## Sector Playbook\n\n{playbook}"]
    if agent.uses_prior_sections:
        parts.append(_format_prior_sections(prior_sections or []))
    return "".join(parts)


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw


_BRACKET_REF_RE = re.compile(r"\s*\[[^\]]*\]")


def _strip_bracket_refs(text: str) -> str:
    """Remove leaked bracket-style references (e.g. "[25]", "[2.03.01]",
    "[Manual Setorial §1]") that the LLM sometimes writes inline despite the
    prompt rule — citation_ids/basis are the only valid channels for these.
    """
    cleaned = _BRACKET_REF_RE.sub("", text)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _parse_section_output(raw: str, agent: AgentDef, evidence: _EvidenceIndex) -> SectionOutput:
    try:
        data = json.loads(_strip_json_fences(raw))
        raw_statements = data.get("statements", [])
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning("Failed to parse section output for %s: %s", agent.section_id, e)
        raw_statements = []

    statements = []
    for s in raw_statements:
        if not isinstance(s, dict):
            continue
        stype = s.get("type")
        text = s.get("text")
        if stype not in ("fact", "inference", "judgment") or not text:
            continue

        # Models sometimes return a single string instead of a list here.
        derived_from = s.get("derived_from")
        if isinstance(derived_from, str):
            derived_from = [derived_from]
        elif not isinstance(derived_from, list):
            derived_from = None
        if derived_from:
            derived_from = [_strip_bracket_refs(str(d)) for d in derived_from]

        basis = s.get("basis")
        if basis:
            basis = _strip_bracket_refs(str(basis))

        statements.append(
            TaggedStatement(
                type=stype,
                text=_strip_bracket_refs(str(text)),
                citations=evidence.resolve(s.get("citation_ids") or []),
                derived_from=derived_from or None,
                basis=basis or None,
            )
        )

    return SectionOutput(section_id=agent.section_id, title=agent.title, statements=statements)


async def _generate_section(
    dossier: CompanyDossier,
    playbook: str,
    agent: AgentDef,
    prior_sections: list[SectionOutput] | None = None,
) -> SectionOutput:
    context, evidence = _build_dossier_context(dossier)

    if agent.focus_fre_sections:
        missing = [s for s in agent.focus_fre_sections if s in dossier.coverage.fre_sections_missing]
        if missing:
            query = f"{dossier.trade_name or dossier.name} {agent.title}"
            extra = await _fallback_search(dossier, query)
            if extra:
                context += _format_fallback_evidence(extra, evidence)

    system_prompt = _build_system_prompt(agent)
    user_message = _build_user_message(dossier, playbook, context, agent, prior_sections)

    try:
        raw = await chat_completion(system_prompt, user_message, json_mode=True)
    except Exception as e:
        logger.error("Section generation failed for %s: %s", agent.section_id, e)
        return SectionOutput(section_id=agent.section_id, title=agent.title, statements=[])

    return _parse_section_output(raw, agent, evidence)


async def generate_business_segments(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["business_segments"], prior_sections)


async def generate_financial_performance(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["financial_performance"], prior_sections)


async def generate_debt_capital_structure(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["debt_capital_structure"], prior_sections)


async def generate_cash_flow_liquidity(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["cash_flow_liquidity"], prior_sections)


async def generate_risk_contingencies(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["risk_contingencies"], prior_sections)


async def generate_non_gaap_kpis(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["non_gaap_kpis"], prior_sections)


async def generate_governance_ownership(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["governance_ownership"], prior_sections)


async def generate_mit_outlook(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["mit_outlook"], prior_sections)


async def generate_limitations_coverage(
    dossier: CompanyDossier, playbook: str, prior_sections: list[SectionOutput] | None = None
) -> SectionOutput:
    return await _generate_section(dossier, playbook, _AGENTS_BY_ID["limitations_coverage"], prior_sections)


async def generate_all_sections(dossier: CompanyDossier, playbook: str) -> list[SectionOutput]:
    """Run the full v1 roster in the order required by Decision 6:
    agents 1-7 (parallel, capped concurrency), then MIT Outlook (sees 1-7),
    then Limitations & Coverage (sees 1-8)."""
    primary_agents = [a for a in AGENT_ROSTER if not a.uses_prior_sections]
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SECTIONS)
    first_call = True

    async def _run(agent: AgentDef, prior_sections: list[SectionOutput] | None = None) -> SectionOutput:
        nonlocal first_call
        async with semaphore:
            if not first_call:
                await asyncio.sleep(_INTER_SECTION_DELAY_SECONDS)
            first_call = False
            return await _generate_section(dossier, playbook, agent, prior_sections)

    sections = list(await asyncio.gather(*(_run(a) for a in primary_agents)))

    for agent in AGENT_ROSTER:
        if not agent.uses_prior_sections:
            continue
        sections.append(await _run(agent, prior_sections=sections))

    return sections
