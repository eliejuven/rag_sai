"""
LLM-based extraction of qualitative facts and disclosed metrics from FRE
section text.

This is the only place in Phase 1 (Company Dossier) that uses an LLM —
everything financial/numeric (FinancialLineItem) is extracted directly from
CVM's structured CSVs in app/scraper/cvm_client.py, no LLM involved.
"""

import json
import logging
import re

from app.analysis.schemas import (
    Citation,
    CompanyDossier,
    DisclosedMetric,
    ExtractedKPI,
    ExtractedKPIs,
    QualitativeFact,
)
from app.generation.llm import chat_completion

logger = logging.getLogger(__name__)

# FRE section where company-defined non-GAAP metrics are disclosed
# (see CREDIT_SECTIONS in app/scraper/fre_client.py).
DISCLOSED_METRICS_SECTION = "2.5"

# Cap section text sent to the LLM — FRE sections are typically a few pages;
# this avoids blowing the context window on the rare oversized section.
_MAX_SECTION_CHARS = 12000

_FACTS_SYSTEM_PROMPT = """Você é um analista de crédito extraindo fatos discretos e citáveis de uma seção do Formulário de Referência (FRE) de uma companhia aberta brasileira, registrado na CVM.

Tarefa: leia o texto da seção fornecida e extraia uma lista de afirmações factuais discretas, relevantes para análise de crédito (estrutura societária, segmentos de negócio, fatores de risco, contratos relevantes, governança, litígios, política de dividendos, etc.).

Regras:
- Cada item deve ser uma afirmação autossuficiente, próxima ao texto original (não resuma nem interprete — extraia o que está escrito).
- CRÍTICO: Preserve números e unidades monetárias EXATAMENTE como escritos no texto original.
  Por exemplo, se o texto diz "R$ 39.021.033 mil", escreva "R$ 39.021.033 mil" — NÃO mude "mil" para "milhões".
  Se o texto diz "R$20.453.194" sem unidade, reproduza sem unidade também.
- Ignore texto repetitivo, boilerplate jurídico genérico e instruções de preenchimento do formulário.
- Extraia entre 3 e 15 fatos, dependendo da densidade de informação relevante do texto.
- Se o texto não contiver informação relevante para crédito, retorne uma lista vazia.

Responda APENAS com JSON válido no formato exato:
{"facts": ["fato 1", "fato 2", ...]}"""

_METRICS_SYSTEM_PROMPT = """Você é um analista de crédito extraindo métricas não contábeis (non-GAAP) divulgadas pela companhia na seção 2.5 (Medições não contábeis) do Formulário de Referência (FRE).

Tarefa: identifique cada métrica definida pela própria companhia (ex: "EBITDA Ajustado", "EBITDA Recorrente", "Dívida Líquida Ajustada", indicadores operacionais específicos do setor) e extraia, para cada uma:
- label: o nome EXATO usado pela companhia (não normalize para "EBITDA")
- value: valor numérico mais recente mencionado (número puro, sem unidade; null se não houver um valor único claro)
- unit: unidade do valor (ex: "R$ milhões", "%", "R$ bilhões"), ou null
- period_label: período a que o valor se refere (ex: "FY 2024"), ou null
- definition: a definição/metodologia de cálculo da métrica, em texto próximo ao original, ou null se não explicitada

Regras:
- Preserve os nomes e definições exatamente como divulgados pela companhia.
- Se a seção não contiver métricas não contábeis, retorne uma lista vazia.

Responda APENAS com JSON válido no formato exato:
{"metrics": [{"label": "...", "value": ..., "unit": "...", "period_label": "...", "definition": "..."}]}"""


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def extract_qualitative_facts(
    section: str,
    section_label: str,
    text: str,
    citation: Citation,
) -> list[QualitativeFact]:
    """Extract discrete, citable facts from one FRE section's text."""
    if not text.strip():
        return []

    user_message = f"Seção {section} — {section_label}\n\n{text[:_MAX_SECTION_CHARS]}"

    try:
        raw = await chat_completion(_FACTS_SYSTEM_PROMPT, user_message, json_mode=True)
        data = json.loads(_strip_json_fences(raw))
        facts = data.get("facts", [])
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse qualitative facts for section %s: %s", section, e)
        return []
    except Exception as e:
        logger.error("Qualitative fact extraction error for section %s: %s", section, e)
        return []

    return [
        QualitativeFact(
            section=section,
            section_label=section_label,
            text=fact.strip(),
            citation=citation,
        )
        for fact in facts
        if isinstance(fact, str) and fact.strip()
    ]


_KPI_SYSTEM_PROMPT = """\
You are a financial analyst extracting key credit metrics from a Brazilian company's dossier.

You will receive two inputs:
1. Disclosed Metrics — company-defined non-GAAP figures already extracted from FRE section 2.5.
2. Qualitative Facts — discrete statements extracted from other FRE sections that may mention financial figures.

Your task: identify the most recent value for each of the following 4 concepts, regardless of the label used:

- ebitda: Operating profit before interest, taxes, depreciation and amortization.
  Common labels: "EBITDA Ajustado", "LAJIDA Ajustado", "EBITDA Recorrente", "EBITDA Normalizado",
  "LAJIDA", "Lucro antes de juros, impostos, depreciação e amortização".
  Do NOT pick EBITDA margin (%) or leverage ratios (x).

- ebitda_margin: EBITDA expressed as a % of net revenue.
  Common labels: "Margem EBITDA", "Margem LAJIDA", "Margem EBITDA Ajustada".

- net_debt: Total financial debt minus cash and equivalents.
  Common labels: "Dívida Líquida", "Endividamento Líquido", "Dívida Financeira Líquida",
  "Dívida Líquida Ajustada", "Net Debt".
  Do NOT pick the leverage ratio (e.g. "Dívida Líquida/EBITDA").

- leverage: Ratio of net debt to EBITDA.
  Common labels: "DL/EBITDA", "Dívida Líquida/EBITDA", "Dívida Líquida/EBITDA Ajustado",
  "Alavancagem", "Endividamento/EBITDA", "Net Debt/EBITDA", "Índice de Alavancagem".

Rules:
- Extract values ONLY from the data provided. Do NOT compute, estimate, or use external knowledge.
- Return the label EXACTLY as it appears in the source data.
- For value, return the raw numeric value (no units).
- For unit, return the unit string as stated (e.g. "R$ milhões", "R$ bilhões", "R$ mil", "%", "x").
  IMPORTANT: In Brazilian CVM FRE documents, monetary amounts stated without an explicit unit
  (e.g. "R$20.453.194" with no "mil", "milhões", or "bilhões" qualifier) follow the CVM standard
  of R$ thousand (mil). Extract these with unit "R$ mil".
- For period, return the period label as stated (e.g. "FY 2025", "31.12.2024").
- If a concept is not found in the data, set all its fields to null.
- If multiple periods are available for the same concept, prefer the most recent.
- If an "Ajustado" (adjusted) and plain variant exist for the same period, prefer "Ajustado".

Respond ONLY with valid JSON in this exact format:
{
  "ebitda":        {"value": <number|null>, "unit": <string|null>, "label": <string|null>, "period": <string|null>},
  "ebitda_margin": {"value": <number|null>, "unit": <string|null>, "label": <string|null>, "period": <string|null>},
  "net_debt":      {"value": <number|null>, "unit": <string|null>, "label": <string|null>, "period": <string|null>},
  "leverage":      {"value": <number|null>, "unit": <string|null>, "label": <string|null>, "period": <string|null>}
}"""


def _format_dossier_for_kpi_extraction(dossier: CompanyDossier) -> str:
    """Format dossier data as a compact text block for the KPI extraction prompt."""
    lines = ["## Disclosed Metrics (FRE 2.5)"]
    if dossier.disclosed_metrics:
        for m in dossier.disclosed_metrics:
            val = f"{m.value}" if m.value is not None else "N/A"
            unit = m.unit or ""
            lines.append(f"- {m.label}: {val} {unit} ({m.period_label})")
    else:
        lines.append("(none)")

    lines.append("\n## Qualitative Facts (FRE sections — text mentions of financial figures)")
    # Only include facts that contain digits — likely to have financial figures
    numeric_facts = [
        f for f in dossier.qualitative_facts
        if any(c.isdigit() for c in f.text)
    ][:60]  # cap at 60 to avoid blowing the context window
    if numeric_facts:
        for f in numeric_facts:
            lines.append(f"- [{f.section}] {f.text}")
    else:
        lines.append("(none)")

    return "\n".join(lines)


def _parse_kpi(raw: dict | None) -> ExtractedKPI | None:
    """Parse one KPI entry from the LLM JSON response."""
    if not raw or raw.get("value") is None:
        return None
    v = _safe_float(raw.get("value"))
    if v is None:
        return None
    label = raw.get("label") or ""
    if not label:
        return None
    return ExtractedKPI(
        value=v,
        unit=raw.get("unit") or None,
        label=str(label).strip(),
        period=raw.get("period") or None,
    )


async def extract_financial_kpis(dossier: CompanyDossier) -> ExtractedKPIs:
    """
    LLM-powered extraction of the 4 key credit KPIs from dossier data.

    Searches across disclosed_metrics AND qualitative_facts so it works even
    when EBITDA / Net Debt are mentioned in free text rather than structured
    FRE 2.5 entries. Result is cached in CompanyDossier.extracted_kpis.
    """
    user_message = _format_dossier_for_kpi_extraction(dossier)

    try:
        raw = await chat_completion(_KPI_SYSTEM_PROMPT, user_message, json_mode=True)
        data = json.loads(_strip_json_fences(raw))
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("KPI extraction JSON parse error for %s: %s", dossier.cnpj, e)
        return ExtractedKPIs()
    except Exception as e:
        logger.error("KPI extraction failed for %s: %s", dossier.cnpj, e)
        return ExtractedKPIs()

    return ExtractedKPIs(
        ebitda=_parse_kpi(data.get("ebitda")),
        ebitda_margin=_parse_kpi(data.get("ebitda_margin")),
        net_debt=_parse_kpi(data.get("net_debt")),
        leverage=_parse_kpi(data.get("leverage")),
    )


async def extract_disclosed_metrics(
    text: str,
    period_label: str,
    citation: Citation,
) -> list[DisclosedMetric]:
    """Extract company-defined non-GAAP metrics from FRE section 2.5."""
    if not text.strip():
        return []

    user_message = f"Período de referência: {period_label}\n\n{text[:_MAX_SECTION_CHARS]}"

    try:
        raw = await chat_completion(_METRICS_SYSTEM_PROMPT, user_message, json_mode=True)
        data = json.loads(_strip_json_fences(raw))
        metrics = data.get("metrics", [])
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse disclosed metrics: %s", e)
        return []
    except Exception as e:
        logger.error("Disclosed metric extraction error: %s", e)
        return []

    results = []
    for m in metrics:
        if not isinstance(m, dict) or not m.get("label"):
            continue
        results.append(
            DisclosedMetric(
                label=str(m["label"]).strip(),
                value=_safe_float(m.get("value")),
                unit=m.get("unit") or None,
                period_label=m.get("period_label") or period_label,
                definition=m.get("definition") or None,
                citation=citation,
            )
        )
    return results
