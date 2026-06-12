# BCB Macro Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add BCB (Banco Central do Brasil) macro-indicator data as a fourth source — a dedicated `"macro"` intent for direct macro questions, plus a one-line snapshot enrichment injected into every company (RAG/market) query.

**Architecture:** New async module `app/scraper/bcb_client.py` fetches SGS time-series (Selic, IPCA, BRL/USD, IGPM, unemployment, GDP) and Focus Report forecasts via httpx, cached to `data/bcb_cache.json` (366-day TTL). A `"macro"` intent routes pure macro questions through BCB with full cited sections. RAG and market branches get a one-line BCB snapshot appended to their prompt; the BCB chip only appears in the response when the LLM actually cites it.

**Tech Stack:** Python 3.11, httpx 0.28 (async HTTP, already in requirements.txt), FastAPI SSE, vanilla JS frontend.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/scraper/bcb_client.py` | **Create** | Fetch, cache, format all BCB data |
| `test_bcb_client.py` | **Create** | Standalone async test script |
| `app/query/intent.py` | **Modify** | Add `"macro"` intent |
| `app/generation/prompts.py` | **Modify** | Add `BCB_SYSTEM_PROMPT`, `build_macro_prompt()` |
| `app/routers/query.py` | **Modify** | Macro branch + enrichment in RAG + market branches |
| `app/static/index.html` | **Modify** | BCB badge CSS + detection |
| `.gitignore` | **Modify** | Add `data/bcb_cache.json` |

---

## Task 1: `bcb_client.py` — cache layer and format functions

These two pieces are testable without any network calls.

**Files:**
- Create: `app/scraper/bcb_client.py`
- Create: `test_bcb_client.py` (partial — offline tests only this task)

- [ ] **Step 1: Write the two offline tests**

Create `test_bcb_client.py`:

```python
"""Standalone test script for app/scraper/bcb_client.py"""
import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


def test_cache():
    import app.scraper.bcb_client as bcb

    original_path = bcb.CACHE_PATH
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        bcb.CACHE_PATH = tmp_path
        tmp_path.unlink()  # start with no file

        assert bcb._load_cache() is None, "Expected None for missing cache"

        mock_data = {
            "series": {"selic": [{"data": "01/06/2026", "valor": "10.50"}]},
            "focus": {},
        }
        bcb._save_cache(mock_data)

        cache = bcb._load_cache()
        assert cache is not None
        assert "fetched_at" in cache
        assert cache["data"] == mock_data
        assert not bcb._is_stale(cache), "Fresh cache should not be stale"

        # Backdate the timestamp → should be stale
        old_payload = {
            "fetched_at": (datetime.now() - timedelta(days=400)).isoformat(),
            "data": mock_data,
        }
        tmp_path.write_text(json.dumps(old_payload))
        assert bcb._is_stale(bcb._load_cache()), "400-day-old cache should be stale"

        print("✓ test_cache")
    finally:
        bcb.CACHE_PATH = original_path
        if tmp_path.exists():
            tmp_path.unlink()


def test_format_sections():
    from app.scraper.bcb_client import format_macro_sections

    mock_data = {
        "series": {
            "selic":        [{"data": "01/06/2026", "valor": "10.50"}],
            "ipca":         [{"data": "01/06/2026", "valor": "5.06"}],
            "brl_usd":      [{"data": "01/06/2026", "valor": "5.73"}],
            "igpm":         [{"data": "01/06/2026", "valor": "4.12"}],
            "unemployment": [{"data": "01/06/2026", "valor": "6.20"}],
            "gdp":          [{"data": "01/06/2026", "valor": "2.90"}],
        },
        "focus": {
            "ipca":    {"valor": 4.5,  "data": "2026-06-10", "ano": 2026},
            "selic":   {"valor": 10.5, "data": "2026-06-10", "ano": 2026},
            "brl_usd": {"valor": 5.8,  "data": "2026-06-10", "ano": 2026},
            "pib":     {"valor": 2.1,  "data": "2026-06-10", "ano": 2026},
        },
    }

    sections = format_macro_sections(mock_data)
    assert len(sections) == 5, f"Expected 5 sections, got {len(sections)}"
    assert sections[0]["section"] == "Política Monetária"
    assert sections[1]["section"] == "Inflação"
    assert sections[2]["section"] == "Câmbio"
    assert sections[3]["section"] == "Atividade Econômica"
    assert sections[4]["section"] == "Focus Report"
    assert "10.50" in sections[0]["text"]
    assert "5.06" in sections[1]["text"]
    assert "4.12" in sections[1]["text"]   # IGPM in Inflação section
    assert "5.73" in sections[2]["text"]
    assert "2.90" in sections[3]["text"]   # GDP
    assert "6.20" in sections[3]["text"]   # unemployment
    assert "4.5"  in sections[4]["text"]   # Focus IPCA mediana
    print("✓ test_format_sections")


async def main():
    print("=== test_bcb_client.py ===\n")
    test_cache()
    test_format_sections()
    print("\nOffline tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run tests — verify they fail with ImportError**

```bash
python3 test_bcb_client.py
```

Expected: `ModuleNotFoundError: No module named 'app.scraper.bcb_client'`

- [ ] **Step 3: Create `app/scraper/bcb_client.py` with cache + format functions**

```python
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 test_bcb_client.py
```

Expected output:
```
=== test_bcb_client.py ===

✓ test_cache
✓ test_format_sections

Offline tests passed.
```

- [ ] **Step 5: Commit**

```bash
git add app/scraper/bcb_client.py test_bcb_client.py
git commit -m "feat: add bcb_client cache layer and format functions"
```

---

## Task 2: `bcb_client.py` — SGS series fetching

**Files:**
- Modify: `app/scraper/bcb_client.py` (add `_fetch_series`, `_fetch_all`, `get_macro_data`)
- Modify: `test_bcb_client.py` (add `test_fetch_series`)

- [ ] **Step 1: Add `test_fetch_series` to the test file**

In `test_bcb_client.py`, add this function before `main()`:

```python
async def test_fetch_series():
    from app.scraper.bcb_client import _fetch_series

    readings = await _fetch_series("selic")
    assert isinstance(readings, list), f"Expected list, got {type(readings)}"
    assert len(readings) == 12, f"Expected 12 readings, got {len(readings)}"
    for r in readings:
        assert "data" in r, f"Missing 'data' key in reading: {r}"
        assert "valor" in r, f"Missing 'valor' key in reading: {r}"
    print(f"✓ test_fetch_series  latest={readings[-1]}")
```

And update `main()` to call it:

```python
async def main():
    print("=== test_bcb_client.py ===\n")
    test_cache()
    test_format_sections()
    await test_fetch_series()
    print("\nAll tests passed.")
```

- [ ] **Step 2: Run — verify it fails**

```bash
python3 test_bcb_client.py
```

Expected: `ImportError: cannot import name '_fetch_series' from 'app.scraper.bcb_client'`

- [ ] **Step 3: Add `_fetch_series`, `_fetch_all`, and `get_macro_data` to `bcb_client.py`**

Add these functions after `_is_stale()` and before `format_macro_sections()`:

```python
# ---------------------------------------------------------------------------
# Network fetchers
# ---------------------------------------------------------------------------

async def _fetch_series(key: str) -> list[dict]:
    """Fetch last 12 monthly readings for one SGS series. Returns raw BCB JSON."""
    code, _, _ = SGS_SERIES[key]
    url = _SGS_URL.format(code=code)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()  # [{"data": "DD/MM/YYYY", "valor": "10.50"}, ...]


async def _fetch_focus(indicator_key: str) -> dict | None:
    """Fetch latest median Focus Report forecast for one indicator."""
    bcb_name = FOCUS_INDICATORS[indicator_key]
    params = {
        "$filter": f"Indicador eq '{bcb_name}' and Suavizado eq 'S'",
        "$orderby": "Data desc",
        "$top": "1",
        "$select": "Indicador,Data,Ano,Mediana",
        "$format": "json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(_FOCUS_URL, params=params)
        r.raise_for_status()
        items = r.json().get("value", [])
        if not items:
            return None
        item = items[0]
        return {"valor": item["Mediana"], "data": item["Data"], "ano": item["Ano"]}


async def _fetch_all() -> dict:
    """Fetch all SGS series and Focus forecasts in parallel."""
    import asyncio as _asyncio

    series_keys = list(SGS_SERIES.keys())
    focus_keys = list(FOCUS_INDICATORS.keys())

    series_results, focus_results = await _asyncio.gather(
        _asyncio.gather(*[_fetch_series(k) for k in series_keys], return_exceptions=True),
        _asyncio.gather(*[_fetch_focus(k) for k in focus_keys], return_exceptions=True),
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 test_bcb_client.py
```

Expected (the live call may take 2–3 seconds):
```
=== test_bcb_client.py ===

✓ test_cache
✓ test_format_sections
✓ test_fetch_series  latest={'data': '...', 'valor': '...'}

All tests passed.
```

- [ ] **Step 5: Commit**

```bash
git add app/scraper/bcb_client.py test_bcb_client.py
git commit -m "feat: add bcb series fetching and get_macro_data"
```

---

## Task 3: `bcb_client.py` — Focus Report and snapshot line

**Files:**
- Modify: `app/scraper/bcb_client.py` (add `get_macro_snapshot_line`)
- Modify: `test_bcb_client.py` (add `test_fetch_focus`, `test_snapshot_line`)

- [ ] **Step 1: Add `test_fetch_focus` and `test_snapshot_line` to the test file**

Add both functions before `main()`:

```python
async def test_fetch_focus():
    from app.scraper.bcb_client import _fetch_focus

    result = await _fetch_focus("ipca")
    assert result is not None, "Expected Focus data for IPCA, got None"
    assert "valor" in result, f"Missing 'valor': {result}"
    assert "data"  in result, f"Missing 'data': {result}"
    assert "ano"   in result, f"Missing 'ano': {result}"
    print(f"✓ test_fetch_focus  ipca={result}")


async def test_snapshot_line():
    from app.scraper.bcb_client import get_macro_snapshot_line

    line = await get_macro_snapshot_line()
    for label in ("Selic", "IPCA", "BRL/USD", "IGPM", "Desemprego", "PIB"):
        assert label in line, f"Missing '{label}' in snapshot line: {line}"
    assert "|" in line, "Expected pipe-separated values"
    print(f"✓ test_snapshot_line  {line}")
```

Update `main()`:

```python
async def main():
    print("=== test_bcb_client.py ===\n")
    test_cache()
    test_format_sections()
    await test_fetch_series()
    await test_fetch_focus()
    await test_snapshot_line()
    print("\nAll tests passed.")
```

- [ ] **Step 2: Run — verify the two new tests fail**

```bash
python3 test_bcb_client.py
```

Expected: `ImportError: cannot import name 'get_macro_snapshot_line'`

- [ ] **Step 3: Add `get_macro_snapshot_line` to `bcb_client.py`**

Add after `get_macro_data()`:

```python
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
```

- [ ] **Step 4: Run all tests**

```bash
python3 test_bcb_client.py
```

Expected:
```
=== test_bcb_client.py ===

✓ test_cache
✓ test_format_sections
✓ test_fetch_series  latest={...}
✓ test_fetch_focus   ipca={...}
✓ test_snapshot_line  Selic: 10.50% | IPCA: 5.06% | BRL/USD: R$ 5.73 | ...

All tests passed.
```

- [ ] **Step 5: Commit**

```bash
git add app/scraper/bcb_client.py test_bcb_client.py
git commit -m "feat: add focus report fetch and macro snapshot line"
```

---

## Task 4: Intent — add `"macro"`

**Files:**
- Modify: `app/query/intent.py`

- [ ] **Step 1: Replace the entire content of `app/query/intent.py`**

```python
from app.generation.llm import chat_completion

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a document Q&A system about Brazilian listed companies.
Given a user message, determine the intent.

Reply with exactly one word:
- "macro" if the user is asking about macroeconomic indicators: Selic, taxa de juros, IPCA, inflação, câmbio, BRL/USD, dólar, IGPM, desemprego, PIB, crescimento econômico, or Focus Report forecasts (projeção, expectativa, previsão) for any of these. Even when no specific company is named. Questions that mix a specific company with macro indicators (e.g. "como a Selic afeta a Petrobras?") are NOT "macro" — they are "search".
- "market" if the user is asking about: stock price, current price, share price, market cap (capitalização de mercado), price history, cotação, preço atual, preço da ação, valor de mercado, histórico de preços, alta/baixa, máxima/mínima, 52 semanas, valorização, desvalorização, or how a stock is currently trading. Even if the company is unknown or fictitious, questions about price/cotação/market cap are always "market". Market cap and current valuation ARE market questions, not accounting questions.
- "search" if the user is asking about financial statements, revenues (receita), profits (lucro), EBITDA, balance sheets (balanço), debts (dívida), dividends (dividendos), or other accounting/document-based data from regulatory filings. Also use "search" for questions mixing a specific company with macro context.
- "chat" if the user is making casual conversation, greeting, or asking something completely unrelated to financial data (e.g. "hello", "thanks", "how are you").

Examples:
- "Qual a Selic atual?" → macro
- "Como está a inflação no Brasil?" → macro
- "Qual a previsão do IPCA para 2025?" → macro
- "Qual o câmbio hoje?" → macro
- "Como a Selic afeta os lucros do Itaú?" → search
- "Qual o preço atual da Vale?" → market
- "Qual o market cap da Ambev?" → market
- "Qual é a capitalização de mercado da Petrobras?" → market
- "Histórico de preços do Itaú nos últimos 2 anos" → market
- "Qual o preço da Empresa Fictícia XYZ?" → market
- "Qual o EBITDA da Petrobras em 2023?" → search
- "Qual a receita da Magazine Luiza?" → search
- "Olá, tudo bem?" → chat

Reply with ONLY "macro", "market", "search", or "chat", nothing else."""


async def detect_intent(question: str) -> str:
    """Classify question intent. Returns "macro", "search", "market", or "chat"."""
    response = await chat_completion(INTENT_SYSTEM_PROMPT, question)
    intent = response.strip().lower()

    if intent not in ("search", "chat", "market", "macro"):
        return "search"

    return intent
```

- [ ] **Step 2: Commit**

```bash
git add app/query/intent.py
git commit -m "feat: add macro intent to intent classifier"
```

---

## Task 5: Prompts — `BCB_SYSTEM_PROMPT` and `build_macro_prompt`

**Files:**
- Modify: `app/generation/prompts.py`

- [ ] **Step 1: Add BCB prompt constants and builder to `app/generation/prompts.py`**

Add at the end of the file:

```python
BCB_SYSTEM_PROMPT = """You are a helpful assistant that answers questions about Brazilian macroeconomic indicators.
Answer ONLY based on the BCB (Banco Central do Brasil) data provided. Do not make up figures.
If a specific value is missing from the data, say so clearly. Be concise and direct.
Always cite the section you are drawing from using [1], [2], [3], [4], or [5] when referencing specific figures."""


def build_macro_prompt(question: str, sections: list[dict]) -> str:
    """Build the user message for a macro data query, with numbered sections for citation."""
    context = "\n\n".join(
        f"[{i + 1}] {sec['section']}:\n{sec['text']}"
        for i, sec in enumerate(sections)
    )
    return f"""BCB data (Banco Central do Brasil):

{context}

---

Question: {question}"""
```

- [ ] **Step 2: Commit**

```bash
git add app/generation/prompts.py
git commit -m "feat: add BCB_SYSTEM_PROMPT and build_macro_prompt"
```

---

## Task 6: Router — macro intent branch

**Files:**
- Modify: `app/routers/query.py`

- [ ] **Step 1: Add imports at the top of `app/routers/query.py`**

Change the existing prompts import line from:
```python
from app.generation.prompts import build_system_prompt, build_rag_prompt, build_market_prompt, RAG_SYSTEM_PROMPT, MARKET_SYSTEM_PROMPT
```
to:
```python
from app.generation.prompts import (
    build_system_prompt, build_rag_prompt, build_market_prompt, build_macro_prompt,
    RAG_SYSTEM_PROMPT, MARKET_SYSTEM_PROMPT, BCB_SYSTEM_PROMPT,
)
```

Add a new import line after the `format_market_sections` import:
```python
from app.scraper.bcb_client import get_macro_data, get_macro_snapshot_line, format_macro_sections
```

- [ ] **Step 2: Add `_build_bcb_chunks` helper**

Add after `_build_market_chunks()` (around line 43):

```python
def _build_bcb_chunks(sections: list[dict]) -> list[ChunkResult]:
    """Convert BCB macro sections into ChunkResult objects for frontend citation."""
    return [
        ChunkResult(
            text=sec["text"],
            filename=f"BCB — {sec['section']}",
            page_number=0,
            score=1.0,
        )
        for sec in sections
    ]
```

- [ ] **Step 3: Add macro branch to the non-SSE `/query` endpoint**

In `query_knowledge_base()`, add after the `if intent == "market":` block (before `if vector_store.size == 0:`):

```python
    if intent == "macro":
        macro_data = await get_macro_data()
        sections = format_macro_sections(macro_data)
        user_message = build_macro_prompt(question, sections)
        answer = await chat_completion(BCB_SYSTEM_PROMPT, user_message, temperature=0.2)
        return QueryResponse(answer=answer, grounded=True, chunks=_build_bcb_chunks(sections))
```

- [ ] **Step 4: Add macro branch to the SSE `/query/stream` endpoint**

In `run_pipeline()`, add after the `if intent == "market":` block (before `# ---- 2. Extract company and year`):

```python
            if intent == "macro":
                await emit("Consultando dados macroeconômicos do Banco Central...")
                macro_data = await get_macro_data()
                await emit("Gerando resposta...")
                sections = format_macro_sections(macro_data)
                user_message = build_macro_prompt(question, sections)
                answer = await chat_completion(BCB_SYSTEM_PROMPT, user_message, temperature=0.2)
                await queue.put({
                    "type": "answer",
                    "answer": answer,
                    "grounded": True,
                    "chunks": [c.model_dump() for c in _build_bcb_chunks(sections)],
                })
                return
```

- [ ] **Step 5: Smoke-test by starting the server and asking a macro question**

```bash
source .venv/bin/activate
python3 -m uvicorn app.main:app --reload --port 8001
```

In another terminal:
```bash
curl -s -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Qual a Selic atual?"}' | python3 -m json.tool
```

Expected: `grounded: true`, `chunks` contains 5 items with filenames starting `"BCB — "`.

- [ ] **Step 6: Commit**

```bash
git add app/routers/query.py
git commit -m "feat: add macro intent branch to query router"
```

---

## Task 7: Router — BCB enrichment in the RAG branch

This modifies `_search_and_answer` to append the BCB snapshot as a numbered section and conditionally include it as a citation chunk.

**Files:**
- Modify: `app/routers/query.py` lines 122–126 (the tail of `_search_and_answer`)

- [ ] **Step 1: Replace the final block of `_search_and_answer`**

Find this block (currently lines 122–126):
```python
    user_message = build_rag_prompt(question, chunk_dicts, alias_hint=alias_hint)
    system_prompt = build_system_prompt(chunk_dicts)
    answer = await chat_completion(system_prompt, user_message, temperature=0.2)

    return QueryResponse(answer=answer, grounded=True, chunks=chunks)
```

Replace with:
```python
    user_message = build_rag_prompt(question, chunk_dicts, alias_hint=alias_hint)

    # BCB enrichment: append macro snapshot as a numbered section
    snapshot_line = await get_macro_snapshot_line()
    bcb_section_num = len(chunk_dicts) + 1
    user_message += f"\n\n[{bcb_section_num}] Contexto Macroeconômico (BCB):\n{snapshot_line}"

    system_prompt = build_system_prompt(chunk_dicts)
    answer = await chat_completion(system_prompt, user_message, temperature=0.2)

    # Include BCB chip only when the LLM actually cited the macro section
    if f"[{bcb_section_num}]" in answer:
        chunks.append(ChunkResult(
            text=f"Contexto Macroeconômico (BCB):\n{snapshot_line}",
            filename="BCB — Macro Snapshot",
            page_number=0,
            score=1.0,
        ))

    return QueryResponse(answer=answer, grounded=True, chunks=chunks)
```

- [ ] **Step 2: Smoke-test**

With the server still running:
```bash
curl -s -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Qual o EBITDA da Petrobras em 2023?"}' | python3 -m json.tool
```

Expected: response is grounded, chunks are CVM document chunks. If the LLM mentioned Selic/macro context and cited the BCB section, a `"BCB — Macro Snapshot"` chunk also appears.

- [ ] **Step 3: Commit**

```bash
git add app/routers/query.py
git commit -m "feat: inject BCB macro snapshot into RAG branch"
```

---

## Task 8: Router — BCB enrichment in the market branch

Same enrichment pattern applied to both market handlers.

**Files:**
- Modify: `app/routers/query.py` — two market blocks

- [ ] **Step 1: Update the non-SSE market block**

Find this block in `query_knowledge_base()`:
```python
                    sections = format_market_sections(data, company_name, ticker)
                    user_message = build_market_prompt(question, sections, alias_hint=alias_hint)
                    answer = await chat_completion(MARKET_SYSTEM_PROMPT, user_message, temperature=0.2)
                    market_chunks = _build_market_chunks(sections, has_data)
                    return QueryResponse(answer=answer, grounded=has_data, chunks=market_chunks)
```

Replace with:
```python
                    sections = format_market_sections(data, company_name, ticker)
                    user_message = build_market_prompt(question, sections, alias_hint=alias_hint)
                    snapshot_line = await get_macro_snapshot_line()
                    bcb_section_num = len(sections) + 1
                    user_message += f"\n\n[{bcb_section_num}] Contexto Macroeconômico (BCB):\n{snapshot_line}"
                    answer = await chat_completion(MARKET_SYSTEM_PROMPT, user_message, temperature=0.2)
                    market_chunks = _build_market_chunks(sections, has_data)
                    if f"[{bcb_section_num}]" in answer:
                        market_chunks.append(ChunkResult(
                            text=f"Contexto Macroeconômico (BCB):\n{snapshot_line}",
                            filename="BCB — Macro Snapshot",
                            page_number=0,
                            score=1.0,
                        ))
                    return QueryResponse(answer=answer, grounded=has_data, chunks=market_chunks)
```

- [ ] **Step 2: Update the SSE market block**

Find this block in `run_pipeline()`:
```python
                await emit("Gerando resposta...")
                sections = format_market_sections(data, company_name, ticker)
                snap = data["snapshot"]
                has_data = any(snap.get(k) is not None for k in ("price", "market_cap", "pe_ratio"))
                user_message = build_market_prompt(question, sections, alias_hint=alias_hint)
                answer = await chat_completion(MARKET_SYSTEM_PROMPT, user_message, temperature=0.2)
                market_chunks = _build_market_chunks(sections, has_data)
                await queue.put({
                    "type": "answer",
                    "answer": answer,
                    "grounded": has_data,
                    "chunks": [c.model_dump() for c in market_chunks],
                })
                return
```

Replace with:
```python
                await emit("Gerando resposta...")
                sections = format_market_sections(data, company_name, ticker)
                snap = data["snapshot"]
                has_data = any(snap.get(k) is not None for k in ("price", "market_cap", "pe_ratio"))
                user_message = build_market_prompt(question, sections, alias_hint=alias_hint)
                snapshot_line = await get_macro_snapshot_line()
                bcb_section_num = len(sections) + 1
                user_message += f"\n\n[{bcb_section_num}] Contexto Macroeconômico (BCB):\n{snapshot_line}"
                answer = await chat_completion(MARKET_SYSTEM_PROMPT, user_message, temperature=0.2)
                market_chunks = _build_market_chunks(sections, has_data)
                if f"[{bcb_section_num}]" in answer:
                    market_chunks.append(ChunkResult(
                        text=f"Contexto Macroeconômico (BCB):\n{snapshot_line}",
                        filename="BCB — Macro Snapshot",
                        page_number=0,
                        score=1.0,
                    ))
                await queue.put({
                    "type": "answer",
                    "answer": answer,
                    "grounded": has_data,
                    "chunks": [c.model_dump() for c in market_chunks],
                })
                return
```

- [ ] **Step 3: Commit**

```bash
git add app/routers/query.py
git commit -m "feat: inject BCB macro snapshot into market branch"
```

---

## Task 9: Frontend — BCB badge

**Files:**
- Modify: `app/static/index.html`

- [ ] **Step 1: Add BCB badge CSS**

Find:
```css
        .grounded-badge.market {
            background: #2a1f0a;
            color: #fbbf24;
            border: 1px solid #5a4010;
        }
```

Add after it:
```css
        .grounded-badge.bcb {
            background: #0a2a2a;
            color: #34d399;
            border: 1px solid #0d5a3a;
        }
```

- [ ] **Step 2: Add BCB badge detection**

Find:
```javascript
                const isCvm = (chunks || []).some(c => c.filename && c.filename.includes("(CVM)"));
                const isMarket = (chunks || []).some(c => c.filename && c.filename.startsWith("Yahoo Finance"));
                if (isCvm) {
                    html += `<span class="grounded-badge cvm">Grounded in CVM data</span>`;
                } else if (isMarket) {
                    html += `<span class="grounded-badge market">Yahoo Finance</span>`;
                } else {
                    html += `<span class="grounded-badge yes">Grounded in documents</span>`;
                }
```

Replace with:
```javascript
                const isCvm    = (chunks || []).some(c => c.filename && c.filename.includes("(CVM)"));
                const isMarket = (chunks || []).some(c => c.filename && c.filename.startsWith("Yahoo Finance"));
                const isBcb    = chunks && chunks.length > 0 && chunks.every(c => c.filename && c.filename.startsWith("BCB"));
                if (isCvm) {
                    html += `<span class="grounded-badge cvm">Grounded in CVM data</span>`;
                } else if (isMarket) {
                    html += `<span class="grounded-badge market">Yahoo Finance</span>`;
                } else if (isBcb) {
                    html += `<span class="grounded-badge bcb">Banco Central do Brasil</span>`;
                } else {
                    html += `<span class="grounded-badge yes">Grounded in documents</span>`;
                }
```

- [ ] **Step 3: Verify in browser**

With the server running, open `http://localhost:8001` and ask *"Qual a Selic atual?"*. Confirm:
- Green-teal "Banco Central do Brasil" badge appears
- 5 citation chips shown: `[1] BCB — Política Monetária`, `[2] BCB — Inflação`, etc.
- Clicking a chip opens the ref panel showing the raw BCB table data
- Inline citations like `[1]` in the answer text are clickable

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add BCB badge to frontend"
```

---

## Task 10: Gitignore and CLAUDE.md

**Files:**
- Modify: `.gitignore`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `data/bcb_cache.json` to `.gitignore`**

Add after the `uploads/` line:
```
# Runtime data
data/bcb_cache.json
```

- [ ] **Step 2: Update `CLAUDE.md` — Commands section**

Add `python3 test_bcb_client.py` to the test commands list.

- [ ] **Step 3: Update `CLAUDE.md` — Architecture section**

In the **Request flow** section, change the intent line from:

> **Intent** (`app/query/intent.py`) — LLM classifies into three intents: `"search"`, `"market"`, or `"chat"`.

To:

> **Intent** (`app/query/intent.py`) — LLM classifies into four intents: `"search"`, `"market"`, `"macro"`, or `"chat"`.

Add a new **BCB macro branch** section after the **Market data branch** section:

```markdown
### BCB macro branch

When intent is `"macro"`, the pipeline fetches from the Brazilian Central Bank (BCB) API:

- `app/scraper/bcb_client.py` fetches 6 SGS time-series (Selic, IPCA, BRL/USD, IGPM, unemployment, GDP) and Focus Report consensus forecasts via `httpx`. All fetches run in parallel with `asyncio.gather`.
- Results are cached to `data/bcb_cache.json` with a **366-day TTL** (persists across restarts). `get_macro_data()` reads from cache when fresh; fetches and writes on first use or expiry; falls back to stale cache if the live fetch fails.
- `format_macro_sections()` returns 5 named sections (`[1] Política Monetária`, `[2] Inflação`, `[3] Câmbio`, `[4] Atividade Econômica`, `[5] Focus Report`) passed to `build_macro_prompt()` for numbered citations.
- **Enrichment**: every RAG and market response also receives a one-line BCB snapshot (`get_macro_snapshot_line()`) as a numbered section appended to the prompt. A `"BCB — Macro Snapshot"` chip is only included in the response when the LLM's answer actually cites that section number.
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore CLAUDE.md
git commit -m "chore: gitignore bcb cache, update CLAUDE.md for BCB feature"
```
