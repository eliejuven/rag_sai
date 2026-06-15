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
    "DRE_con": "Demonstração de Resultado (DRE)",
    "BPA_con": "Balanço Patrimonial Ativo (BPA)",
    "BPP_con": "Balanço Patrimonial Passivo (BPP)",
    "DFC_MI_con": "Demonstração de Fluxo de Caixa - Método Indireto (DFC)",
    "DFC_MD_con": "Demonstração de Fluxo de Caixa - Método Direto (DFC)",
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
        return "(nenhum dado financeiro estruturado disponível)"

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
        lines = [f"### {label}", "", f"| Conta | Descrição | {header_cols} |"]
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
        return "(nenhuma métrica não-GAAP extraída)"
    lines = []
    for m in metrics:
        cid = evidence.id_for(m.citation)
        value = f"{m.value:,.2f}" if m.value is not None else "n/d"
        unit = m.unit or ""
        definition = f" — definição: {m.definition}" if m.definition else ""
        lines.append(f"- **{m.label}** ({m.period_label}): {value} {unit}{definition} [{cid}]")
    return "\n".join(lines)


def _format_qualitative_facts(facts: list[QualitativeFact], evidence: _EvidenceIndex) -> str:
    if not facts:
        return "(nenhum fato qualitativo extraído)"
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
        return "(nenhum conflito de dados identificado)"
    return "\n".join(f"- {c.description}" for c in dossier.conflicts)


def _format_coverage(dossier: CompanyDossier) -> str:
    cov = dossier.coverage
    return (
        f"- Anos com DFP (anual): {cov.dfp_years or 'nenhum'}\n"
        f"- Anos com ITR (trimestral): {cov.itr_years or 'nenhum'}\n"
        f"- Anos com FRE: {cov.fre_years or 'nenhum'}\n"
        f"- Seções do FRE presentes: {', '.join(cov.fre_sections_present) or 'nenhuma'}\n"
        f"- Seções do FRE ausentes: {', '.join(cov.fre_sections_missing) or 'nenhuma'}"
    )


def _build_dossier_context(dossier: CompanyDossier) -> tuple[str, _EvidenceIndex]:
    """Render the full Dossier as Markdown with inline [N] source markers."""
    evidence = _EvidenceIndex()

    header = (
        f"# Dossiê — {dossier.name} ({dossier.trade_name})\n"
        f"CNPJ: {dossier.cnpj} | CD_CVM: {dossier.cd_cvm} | Setor: {dossier.sector or 'n/d'}\n"
        f"Gerado em: {dossier.generated_at.isoformat()}\n\n"
        f"## Cobertura de dados\n{_format_coverage(dossier)}"
    )

    financials = _format_financial_statements(dossier.financial_line_items, evidence)
    metrics = _format_disclosed_metrics(dossier.disclosed_metrics, evidence)
    facts = _format_qualitative_facts(dossier.qualitative_facts, evidence)
    conflicts = _format_conflicts(dossier)

    context = (
        f"{header}\n\n"
        f"## Demonstrações financeiras (DFP/ITR)\n\n{financials}\n\n"
        f"## Métricas não-GAAP divulgadas (FRE 2.5)\n\n{metrics}\n\n"
        f"## Fatos qualitativos (FRE)\n\n{facts}\n\n"
        f"## Conflitos de dados\n\n{conflicts}"
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
    lines = ["\n\n## Evidência adicional (busca complementar)\n"]
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


_SYSTEM_TEMPLATE = """Você é um analista de crédito sênior, atuando como {role}, analisando uma companhia aberta brasileira registrada na CVM.

Você recebe um "Dossiê da Companhia" em Markdown — demonstrações financeiras (DFP/ITR, múltiplos períodos), métricas não-GAAP e fatos qualitativos extraídos do Formulário de Referência (FRE). Cada dado relevante termina com um identificador de fonte entre colchetes, ex: [12] — esses identificadores referenciam citações reais já presentes no Dossiê.{extra}

Sua tarefa: {task}

Regras:
- "fact": afirmação extraída diretamente do Dossiê. "citation_ids" deve conter pelo menos um identificador EXATAMENTE como aparece no Dossiê (nunca invente um número).
- "inference": conclusão derivada por raciocínio/cálculo a partir de fatos do Dossiê. Preencha "derived_from" com um breve resumo dos fatos combinados; inclua "citation_ids" dos fatos-base sempre que possível.
- "judgment": julgamento analítico fundamentado no Manual Setorial (Sector Playbook). Preencha "basis" indicando qual seção do manual orientou o julgamento (ex.: "Manual Setorial §1").
- NUNCA escreva referências entre colchetes (ex.: "[25]", "[2.03.01]", "[Manual Setorial §1]") dentro de "text", "derived_from" ou "basis" — esses campos devem ser texto corrido, sem colchetes. Identificadores de fonte (os números entre colchetes do Dossiê) vão APENAS em "citation_ids"; a seção do Manual Setorial vai apenas em "basis", como texto (ex.: "Manual Setorial §1"), sem colchetes.
- Para métricas como EBITDA/LAJIDA (ajustado ou não), dívida líquida e similares: se a companhia já divulga esse valor em "Métricas não-GAAP divulgadas (FRE 2.5)", use EXATAMENTE esse valor (com a definição da própria companhia) como "fact" — NUNCA calcule sua própria versão a partir da DRE/BPA/BPP, pois isso gera números conflitantes com outras seções. Só calcule ("inference") uma métrica desse tipo se ela não constar nas Métricas não-GAAP divulgadas.
- Priorize a tendência ao longo dos períodos disponíveis no Dossiê, não apenas o valor mais recente.
- Quando faltar dado relevante, diga isso explicitamente em vez de inventar.
- Produza entre 3 e 10 statements. Responda em português do Brasil.

Responda APENAS com JSON válido, no formato exato:
{{"statements": [{{"type": "fact|inference|judgment", "text": "...", "citation_ids": [1,2], "derived_from": ["..."] ou null, "basis": "..." ou null}}]}}"""

_PRIOR_SECTIONS_HINT = (
    " Você também recebe as seções já produzidas pelos outros agentes — use-as como"
    " contexto adicional, mas não repita o que elas já disseram."
)


AGENT_ROSTER: list[AgentDef] = [
    AgentDef(
        section_id="business_segments",
        title="Negócio e Segmentos",
        role="o agente de Negócio e Segmentos",
        task=(
            "descreva a identidade da companhia, seus principais negócios e segmentos "
            "operacionais, e como eles geram receita — com base no Dossiê (especialmente "
            "FRE 1.2, 1.3, 1.6, 2.10)."
        ),
        focus_fre_sections=["1.2", "1.3", "1.6", "2.10"],
    ),
    AgentDef(
        section_id="financial_performance",
        title="Desempenho Financeiro",
        role="o agente de Desempenho Financeiro",
        task=(
            "analise receita, margens e resultado ao longo dos períodos disponíveis na DRE, "
            "destacando tendências, e relacione com a discussão da administração (FRE 2.1, 2.2) "
            "quando relevante."
        ),
        focus_fre_sections=["2.1", "2.2"],
    ),
    AgentDef(
        section_id="debt_capital_structure",
        title="Estrutura de Capital e Endividamento",
        role="o agente de Estrutura de Capital e Endividamento",
        task=(
            "analise o nível e a trajetória do endividamento, a composição entre dívida de "
            "curto e longo prazo, e o patrimônio líquido (BPA/BPP), cruzando com contratos "
            "relevantes, covenants e condições financeiras descritos no FRE (1.15, 2.1)."
        ),
        focus_fre_sections=["1.15", "2.1"],
    ),
    AgentDef(
        section_id="cash_flow_liquidity",
        title="Fluxo de Caixa e Liquidez",
        role="o agente de Fluxo de Caixa e Liquidez",
        task=(
            "analise a geração de caixa operacional, de investimento e de financiamento (DFC) "
            "ao longo dos períodos disponíveis, e avalie a posição de liquidez da companhia."
        ),
    ),
    AgentDef(
        section_id="risk_contingencies",
        title="Fatores de Risco e Contingências",
        role="o agente de Fatores de Risco e Contingências",
        task=(
            "identifique os principais fatores de risco e contingências divulgados (FRE 4.1, "
            "4.2, 4.3, 4.7, 5.1, 2.8) e, usando o Manual Setorial, julgue quais desses riscos "
            "são mais relevantes para a análise de crédito desta companhia especificamente."
        ),
        focus_fre_sections=["4.1", "4.2", "4.3", "4.7", "5.1", "2.8"],
    ),
    AgentDef(
        section_id="non_gaap_kpis",
        title="Métricas Não-GAAP e KPIs",
        role="o agente de Métricas Não-GAAP e KPIs",
        task=(
            "apresente as métricas não-GAAP divulgadas pela companhia (FRE 2.5), preservando "
            "os nomes e definições exatos, e — quando possível — relacione-as com as linhas "
            "correspondentes da DRE/DFC para dar contexto."
        ),
        focus_fre_sections=["2.5"],
    ),
    AgentDef(
        section_id="governance_ownership",
        title="Governança e Estrutura Societária",
        role="o agente de Governança e Estrutura Societária",
        task=(
            "descreva a estrutura societária, o grupo econômico, o programa de integridade e "
            "a composição dos órgãos de administração (FRE 1.12, 5.3, 6.5, 7.1), destacando "
            "qualquer elemento relevante para risco de crédito (ex.: operações com partes "
            "relacionadas, concentração de controle)."
        ),
        focus_fre_sections=["1.12", "5.3", "6.5", "7.1"],
    ),
    AgentDef(
        section_id="mit_outlook",
        title="Perspectiva (MIT Outlook)",
        role="o agente de Perspectiva — a 'lente MIT' de julgamento analítico sênior",
        task=(
            "com base no Dossiê e nas seções já geradas pelos outros agentes, formule a "
            "perspectiva de crédito da companhia — a opinião analítica que amarra os fatos e "
            "inferências anteriores em um julgamento coerente, seguindo explicitamente o "
            "Manual Setorial (especialmente a seção 5, 'raciocínio característico')."
        ),
        uses_prior_sections=True,
    ),
    AgentDef(
        section_id="limitations_coverage",
        title="Limitações e Cobertura",
        role="o agente de Limitações e Cobertura (meta-análise)",
        task=(
            "com base na cobertura de dados do Dossiê (seções do FRE ausentes, anos "
            "disponíveis), nos conflitos de dados identificados, e nas seções já geradas, "
            "liste explicitamente as limitações desta análise — o que não pôde ser avaliado "
            "por falta de dados, e quaisquer conflitos não resolvidos."
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
    return "\n\n## Seções já geradas\n\n" + "\n\n".join(blocks)


def _build_user_message(
    dossier: CompanyDossier,
    playbook: str,
    context: str,
    agent: AgentDef,
    prior_sections: list[SectionOutput] | None,
) -> str:
    parts = [context, f"\n\n## Manual Setorial (Sector Playbook)\n\n{playbook}"]
    if agent.uses_prior_sections:
        parts.append(_format_prior_sections(prior_sections or []))
    return "".join(parts)


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw


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

        statements.append(
            TaggedStatement(
                type=stype,
                text=str(text).strip(),
                citations=evidence.resolve(s.get("citation_ids") or []),
                derived_from=derived_from or None,
                basis=s.get("basis") or None,
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
