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

Your task: given a user question, extract the name of a Brazilian company (if any).

Rules:
- Extract ANY company name mentioned, whether it is famous or not.
- For well-known aliases, resolve to the official name:
    "Vivo" → "Telefônica Brasil"
    "Magalu" → "Magazine Luiza"
    "Pão de Açúcar" or "GPA" → "Grupo Pão de Açúcar"
    "Bradesco" → "Banco Bradeco"
    "Itaú" → "Itaú Unibanco"
    "BB" → "Banco do Brasil"
    "Petrobras" → "Petrobras"
- For any company you do NOT recognise as a famous alias, return its name
  exactly as written in the question (e.g. "Axia Energia", "Copasa", "Transmissora Aliança").
- If the question mentions a ticker (e.g. PETR4, ABEV3), return the company
  name instead (e.g. "Petrobras", "Ambev").
- If the question is general (no company mentioned), return null.
- If multiple companies are mentioned, return the most prominent one.

Reply with ONLY valid JSON in this exact format:
{"company_name": "Company Name"} or {"company_name": null}"""


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
