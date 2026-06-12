"""
CVM Company Registry

Downloads the official CVM registry of listed Brazilian companies once and
caches it locally. Exposes lookup_company() to map a free-text company name
or ticker to its CNPJ and official name.

Source: https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv
"""

import io
import logging
import re
import unicodedata
from pathlib import Path

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

REGISTRY_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "cvm_registry.csv"

# Columns we actually need from the registry
_KEEP_COLS = ["CNPJ_CIA", "DENOM_SOCIAL", "DENOM_COMERC", "SIT", "CD_CVM", "SETOR_ATIV"]


def _normalize_cnpj(cnpj: str) -> str:
    return re.sub(r"[./-]", "", cnpj)


def _normalize(text: str) -> str:
    """Lowercase, strip accents, remove non-alphanumeric characters."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _load_registry(force_refresh: bool = False) -> pd.DataFrame:
    """Load the CVM registry, downloading it if not cached or if refresh forced."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not CACHE_PATH.exists() or force_refresh:
        logger.info("Downloading CVM company registry...")
        response = httpx.get(REGISTRY_URL, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        CACHE_PATH.write_bytes(response.content)
        logger.info("Registry cached at %s", CACHE_PATH)

    df = pd.read_csv(
        CACHE_PATH,
        sep=";",
        encoding="latin-1",
        usecols=lambda c: c in _KEEP_COLS,
        dtype=str,
    )
    df = df.fillna("")

    # Keep only active companies (SIT == "ATIVO")
    df = df[df["SIT"] == "ATIVO"].copy()

    # Pre-compute normalized search keys
    df["_norm_social"] = df["DENOM_SOCIAL"].apply(_normalize)
    df["_norm_comerc"] = df["DENOM_COMERC"].apply(_normalize)

    return df.reset_index(drop=True)


# Module-level registry cache (loaded once per process)
_registry: pd.DataFrame | None = None


def _get_registry(force_refresh: bool = False) -> pd.DataFrame:
    global _registry
    if _registry is None or force_refresh:
        _registry = _load_registry(force_refresh)
    return _registry


def lookup_company(query: str, force_refresh: bool = False) -> dict | None:
    """
    Find a company in the CVM registry by name or ticker.

    Args:
        query: Free-text company name (e.g. "Petrobras", "Ambev") or
               ticker (e.g. "PETR4", "ABEV3").
        force_refresh: Re-download the registry even if cached.

    Returns:
        Dict with keys: cnpj, cd_cvm, name, trade_name
        or None if no match found.
    """
    df = _get_registry(force_refresh)
    query_clean = query.strip()

    norm_query = _normalize(query_clean)
    tokens = [t for t in norm_query.split() if len(t) > 1]

    if not tokens:
        return None

    # --- 1. All tokens match on trade name (DENOM_COMERC) ---
    comerc_match = df[
        df["_norm_comerc"].apply(lambda x: all(t in x for t in tokens))
    ]
    if not comerc_match.empty:
        row = comerc_match.iloc[0]
        return _row_to_dict(row)

    # --- 2. All tokens match on legal name (DENOM_SOCIAL) ---
    social_match = df[
        df["_norm_social"].apply(lambda x: all(t in x for t in tokens))
    ]
    if not social_match.empty:
        row = social_match.iloc[0]
        return _row_to_dict(row)

    # --- 3. Partial token match — rank by number of matching tokens ---
    min_tokens = max(1, len(tokens) // 2)
    partial_match = df[
        df["_norm_comerc"].apply(
            lambda x: sum(t in x for t in tokens) >= min_tokens
        )
    ].copy()
    if not partial_match.empty:
        partial_match["_score"] = partial_match["_norm_comerc"].apply(
            lambda x: sum(t in x for t in tokens)
        )
        row = partial_match.sort_values("_score", ascending=False).iloc[0]
        return _row_to_dict(row)

    return None


def get_company_by_cnpj(cnpj: str, force_refresh: bool = False) -> dict | None:
    """
    Look up a company in the CVM registry by exact CNPJ match.

    Returns:
        Dict with keys: cnpj, cd_cvm, name, trade_name, sector
        or None if no match found.
    """
    df = _get_registry(force_refresh)
    cnpj_clean = _normalize_cnpj(cnpj)

    match = df[df["CNPJ_CIA"].apply(_normalize_cnpj) == cnpj_clean]
    if match.empty:
        return None
    return _row_to_dict(match.iloc[0])


def _row_to_dict(row: pd.Series) -> dict:
    return {
        "cnpj": row["CNPJ_CIA"].strip(),
        "cd_cvm": row["CD_CVM"].strip(),
        "name": row["DENOM_SOCIAL"].strip(),
        "trade_name": row["DENOM_COMERC"].strip(),
        "sector": row["SETOR_ATIV"].strip(),
    }
