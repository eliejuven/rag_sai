"""
Company Extractor

Uses the LLM to extract a Brazilian listed company name from a user question
and resolve common brand-name aliases to their official CVM-registered names.

Returns None if no listed company is detected in the question.
"""

import json
import logging
import re

from app.generation.llm import chat_completion

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a Brazilian financial data assistant specializing in B3-listed companies.

Your task: given a user question, extract the name of a Brazilian listed company (if any).

Rules:
- Resolve brand names and nicknames to their official registered name:
    "Vivo" → "Telefônica Brasil"
    "Magalu" or "Magazine Luiza" → "Magazine Luiza"
    "Pão de Açúcar" or "GPA" → "Grupo Pão de Açúcar"
    "Bradesco" → "Banco Bradesco"
    "Itaú" → "Itaú Unibanco"
    "BB" or "Banco do Brasil" → "Banco do Brasil"
    "Petrobras" → "Petrobras"
    "Vale" → "Vale"
    "Ambev" → "Ambev"
    "Embraer" → "Embraer"
    "Eletrobras" → "Eletrobras"
    "Cemig" → "Cemig"
    "Copel" → "Copel"
    "Sabesp" → "Sabesp"
    "Localiza" → "Localiza"
    "Hapvida" → "Hapvida"
    "Fleury" → "Fleury"
    "TOTVS" → "TOTVS"
    "Natura" → "Natura"
    "Arezzo" → "Arezzo"
    "WEG" → "WEG"
    For any other company, return the most commonly used official name.
- If the question mentions a ticker (e.g. PETR4, ABEV3), extract the company name
  (e.g. "Petrobras", "Ambev") — do NOT return the ticker itself.
- If no Brazilian listed company is mentioned, return null for company_name.
- If multiple companies are mentioned, return only the most prominent one
  (the one the question is primarily about).

Reply with ONLY valid JSON in this exact format:
{"company_name": "Official Company Name"} or {"company_name": null}"""


async def extract_company(question: str) -> str | None:
    """
    Extract and resolve a Brazilian listed company name from a user question.

    Returns:
        Official company name string ready for cvm_registry.lookup_company(),
        or None if no company is detected.
    """
    try:
        raw = await chat_completion(_SYSTEM_PROMPT, question, temperature=0.0)
        raw = raw.strip()

        # Strip markdown code fences if the LLM wraps the JSON
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        company_name = data.get("company_name")

        if company_name and isinstance(company_name, str):
            return company_name.strip()
        return None

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse company extractor response: %s | raw: %s", e, raw)
        return None
    except Exception as e:
        logger.error("Company extractor error: %s", e)
        return None
