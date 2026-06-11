"""
BCB Client

Fetches macro-economic indicator data from the Brazilian Central Bank.
Two sources:
  - SGS: time series, last 12 monthly readings per indicator
  - Focus Report (Olinda OData): consensus market forecasts

Async-native. Cache persisted to data/bcb_cache.json with 366-day TTL.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "bcb_cache.json"
CACHE_TTL_DAYS = 366

# SGS series: key → (code, display label, unit)
SGS_SERIES: dict[str, tuple[int, str, str]] = {
    "selic":        (11,    "Taxa Selic",          "%"),
    "ipca":         (433,   "IPCA",                "%"),
    "brl_usd":      (1,     "BRL/USD",             "R$"),
    "igpm":         (189,   "IGPM",                "%"),
    "unemployment": (24369, "Taxa de Desemprego",  "%"),
    "gdp":          (4380,  "PIB (crescimento)",   "%"),
}

# Focus Report indicator names as used by BCB
FOCUS_INDICATORS: dict[str, str] = {
    "ipca":    "IPCA",
    "selic":   "Selic",
    "brl_usd": "Câmbio",
    "pib":     "PIB Total",
}

_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/12?formato=json"
_FOCUS_URL = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais()"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(data: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.now().isoformat(), "data": data}
    CACHE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _is_stale(cache: dict) -> bool:
    fetched_at_str = cache.get("fetched_at")
    if not fetched_at_str:
        return True
    fetched_at = datetime.fromisoformat(fetched_at_str)
    return datetime.now() - fetched_at > timedelta(days=CACHE_TTL_DAYS)


# ---------------------------------------------------------------------------
# Format functions
# ---------------------------------------------------------------------------

def _series_table(series: dict, key: str) -> list[str]:
    """Format last-12-readings as a labelled table. Returns lines."""
    _, label, unit = SGS_SERIES[key]
    readings = series.get(key, [])
    if not readings:
        return [f"{label}: dados indisponíveis."]
    lines = [f"{label} ({unit}):", "Data           Valor"]
    for r in readings:
        lines.append(f"{r['data']}   {r['valor']}")
    return lines


def format_macro_sections(data: dict) -> list[dict]:
    """
    Format BCB data as 5 cited sections for the LLM prompt.
    Returns list of {"section": name, "text": content}.
    Mirrors format_market_sections() in market_data.py.
    """
    series = data.get("series", {})
    focus = data.get("focus", {})
    sections = []

    # [1] Política Monetária
    s1 = ["=== Política Monetária ==="]
    s1.extend(_series_table(series, "selic"))
    sections.append({"section": "Política Monetária", "text": "\n".join(s1)})

    # [2] Inflação
    s2 = ["=== Inflação ==="]
    s2.extend(_series_table(series, "ipca"))
    s2.append("")
    s2.extend(_series_table(series, "igpm"))
    sections.append({"section": "Inflação", "text": "\n".join(s2)})

    # [3] Câmbio
    s3 = ["=== Câmbio ==="]
    s3.extend(_series_table(series, "brl_usd"))
    sections.append({"section": "Câmbio", "text": "\n".join(s3)})

    # [4] Atividade Econômica
    s4 = ["=== Atividade Econômica ==="]
    s4.extend(_series_table(series, "gdp"))
    s4.append("")
    s4.extend(_series_table(series, "unemployment"))
    sections.append({"section": "Atividade Econômica", "text": "\n".join(s4)})

    # [5] Focus Report
    s5 = ["=== Focus Report — Projeções de Mercado ==="]
    focus_labels = {
        "ipca":    "IPCA",
        "selic":   "Selic",
        "brl_usd": "Câmbio (BRL/USD)",
        "pib":     "PIB",
    }
    has_focus = False
    for key, label in focus_labels.items():
        item = focus.get(key)
        if item:
            has_focus = True
            s5.append(f"{label} (mediana, {item['ano']}): {item['valor']} — data: {item['data']}")
    if not has_focus:
        s5.append("Focus Report indisponível.")
    sections.append({"section": "Focus Report", "text": "\n".join(s5)})

    return sections
