"""
BCB Client

Fetches macro-economic indicator data from the Brazilian Central Bank.
Two sources:
  - SGS: time series, last 12 monthly readings per indicator
  - Focus Report (Olinda OData): consensus market forecasts

Async-native. Cache persisted to data/bcb_cache.json with 366-day TTL.
"""

import asyncio
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
    "selic":        (432,   "Taxa Selic",          "%"),
    "ipca":         (433,   "IPCA",                "%"),
    "brl_usd":      (1,     "BRL/USD",             "R$"),
    "igpm":         (189,   "IGPM",                "%"),
    "unemployment": (24369, "Taxa de Desemprego",  "%"),
    "gdp":          (4385,  "PIB (crescimento)",   "%"),
}

# Focus Report indicator names as used by BCB
FOCUS_INDICATORS: dict[str, str] = {
    "ipca":    "IPCA",
    "selic":   "Selic",
    "brl_usd": "Câmbio",
    "pib":     "PIB Total",
}

_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/12?formato=json"
_FOCUS_URL = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais"


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
# Network fetchers
# ---------------------------------------------------------------------------

async def _fetch_series(client: httpx.AsyncClient, key: str) -> list[dict]:
    """Fetch last 12 monthly readings for one SGS series. Returns raw BCB JSON."""
    code, _, _ = SGS_SERIES[key]
    url = _SGS_URL.format(code=code)
    r = await client.get(url)
    r.raise_for_status()
    return r.json()  # [{"data": "DD/MM/YYYY", "valor": "10.50"}, ...]


async def _fetch_focus(client: httpx.AsyncClient, indicator_key: str) -> dict | None:
    """Fetch latest median Focus Report forecast for one indicator.

    The BCB Olinda OData endpoint rejects URL-encoded filter values produced by
    httpx params=, so we build the query string manually using percent-encoding
    only for spaces (%20) and leave single-quotes unescaped.
    """
    bcb_name = FOCUS_INDICATORS[indicator_key]
    # Encode only spaces; single-quotes must stay literal for OData string literals
    bcb_name_enc = bcb_name.replace(" ", "%20")
    qs = (
        f"$top=1"
        f"&$orderby=Data%20desc"
        f"&$format=json"
        f"&$filter=Indicador%20eq%20'{bcb_name_enc}'"
        f"&$select=Indicador,Data,DataReferencia,Mediana"
    )
    url = f"{_FOCUS_URL}?{qs}"
    try:
        r = await client.get(url)
        r.raise_for_status()
        items = r.json().get("value", [])
        if not items:
            return None
        item = items[0]
        return {
            "valor": item["Mediana"],
            "data": item["Data"],
            "ano": item["DataReferencia"],
        }
    except Exception as e:
        logger.warning("BCB Focus fetch failed for %s: %s", indicator_key, e)
        return None


async def _fetch_all() -> dict:
    """Fetch all SGS series and Focus forecasts in parallel."""
    series_keys = list(SGS_SERIES.keys())
    focus_keys = list(FOCUS_INDICATORS.keys())

    async with httpx.AsyncClient(timeout=15.0) as client:
        series_results, focus_results = await asyncio.gather(
            asyncio.gather(*[_fetch_series(client, k) for k in series_keys], return_exceptions=True),
            asyncio.gather(*[_fetch_focus(client, k) for k in focus_keys], return_exceptions=True),
        )

    series = {}
    for key, result in zip(series_keys, series_results):
        if isinstance(result, Exception):
            logger.warning("BCB series fetch failed for %s: %s", key, result)
            series[key] = []
        else:
            series[key] = result

    focus = {}
    for key, result in zip(focus_keys, focus_results):
        if isinstance(result, Exception):
            logger.warning("BCB focus fetch failed for %s: %s", key, result)
            focus[key] = None
        else:
            focus[key] = result

    return {"series": series, "focus": focus}


async def get_macro_data(force_refresh: bool = False) -> dict:
    """Return BCB macro data. Uses disk cache (366-day TTL).
    Falls back to stale cache if a fresh fetch fails."""
    cache = _load_cache()
    if not force_refresh and cache and not _is_stale(cache):
        return cache["data"]

    try:
        fresh = await _fetch_all()
        _save_cache(fresh)
        return fresh
    except Exception as e:
        logger.error("BCB fetch failed: %s", e)
        if cache:
            logger.warning("Using stale BCB cache as fallback")
            return cache["data"]
        raise


async def get_macro_snapshot_line() -> str:
    """Return a compact one-line macro snapshot for prompt enrichment.
    Reads from the 366-day disk cache — near-zero latency on warm runs."""
    data = await get_macro_data()
    series = data.get("series", {})

    def _latest(key: str) -> str | None:
        readings = series.get(key, [])
        return readings[-1]["valor"] if readings else None

    parts = []
    if (v := _latest("selic"))        is not None: parts.append(f"Selic: {v}%")
    if (v := _latest("ipca"))         is not None: parts.append(f"IPCA: {v}%")
    if (v := _latest("brl_usd"))      is not None: parts.append(f"BRL/USD: R$ {v}")
    if (v := _latest("igpm"))         is not None: parts.append(f"IGPM: {v}%")
    if (v := _latest("unemployment")) is not None: parts.append(f"Desemprego: {v}%")
    if (v := _latest("gdp"))          is not None: parts.append(f"PIB: {v}%")

    return " | ".join(parts) if parts else "Dados BCB indisponíveis."


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
