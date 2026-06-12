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

from app.analysis.schemas import Citation, DisclosedMetric, QualitativeFact
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
