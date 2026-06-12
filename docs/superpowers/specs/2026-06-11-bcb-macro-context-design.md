# BCB Macro Context — Design Spec
**Date:** 2026-06-11  
**Branch:** feature/BCB  

---

## Overview

Add Brazilian Central Bank (BCB) macro indicator data as a fourth data source in the RAG pipeline, alongside CVM documents, Yahoo Finance market data, and general LLM knowledge. Two integration modes:

1. **Dedicated `"macro"` intent** — user explicitly asks about macro indicators → BCB API → cited answer.
2. **Enrichment** — every company query (RAG or market) gets a one-line BCB snapshot appended to the prompt; BCB appears as a source chip only when the LLM actually cites it.

---

## Data Sources

### SGS Time Series API
`https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/12?formato=json`

| Indicator | Series code |
|---|---|
| Taxa Selic | 11 |
| IPCA | 433 |
| BRL/USD | 1 |
| IGPM | 189 |
| Unemployment (PNAD) | 24369 |
| GDP growth | 4380 |

Returns last 12 monthly readings per series. Free, official, no API key.

### Focus Report API (Olinda/OData)
`https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais()`

Consensus forecasts from financial institutions for: IPCA, Selic, BRL/USD, PIB. Filtered to the latest available date; returns the median forecast value per indicator.

---

## New Module: `app/scraper/bcb_client.py`

### Cache
- Persisted to `data/bcb_cache.json` (survives server restarts).
- TTL = **366 days**.
- Structure: `{"fetched_at": "<ISO datetime>", "data": { ... }}`.

### Public API

```python
async def get_macro_data(force_refresh: bool = False) -> dict
    # Cache-aware entry point. Reads disk cache; fetches if stale or missing.
    # Returns unified dict with "series" and "focus" keys.

async def get_macro_snapshot_line() -> str
    # Single-line summary for enrichment injection.
    # Calls get_macro_data() internally, formats latest value per indicator.
    # Example: "Selic: 10.50% | IPCA: 5.06% | BRL/USD: 5.73 | IGPM: 4.12% | Desemprego: 6.2% | PIB: 2.9%"

def format_macro_sections(data: dict) -> list[dict]
    # Pure formatting — synchronous. Returns 5 sections: {"section": name, "text": content}
    # Mirrors format_market_sections() — used for macro intent citations.
```

### Citation Sections

| # | `section` key | Content |
|---|---|---|
| [1] | Política Monetária | Selic — last 12 monthly readings |
| [2] | Inflação | IPCA + IGPM — last 12 monthly readings each |
| [3] | Câmbio | BRL/USD — last 12 monthly readings |
| [4] | Atividade Econômica | GDP growth + unemployment — last 12 readings |
| [5] | Focus Report | Consensus forecasts for IPCA, Selic, BRL/USD, PIB |

All fetches run in parallel via `asyncio.gather`. `bcb_client.py` is async-native (unlike `market_data.py` which is synchronous); no `asyncio.to_thread` needed.

---

## Intent Routing

`app/query/intent.py` adds `"macro"` as a fourth intent.

**Routes to `"macro"` when the user asks about:**
- Current or historical values: Selic, taxa de juros, IPCA, inflação, câmbio, BRL/USD, dólar, IGPM, desemprego, PIB, crescimento econômico
- Focus Report forecasts: projeção, expectativa, previsão for any of the above
- Combinations: *"Como está a inflação e a Selic?"*

**Does NOT route to `"macro"`:**
- Company dividend questions (those are `"search"` — dividends are accounting data from filings)
- Stock price questions (those remain `"market"`)
- Questions mixing a specific company with macro context — these stay `"search"` or `"market"`, with BCB data injected as enrichment

---

## Generation Layer

**`app/generation/prompts.py`** additions:

```python
BCB_SYSTEM_PROMPT: str
    # "Answer ONLY based on the BCB data provided. Cite sections [1]–[5]."

def build_macro_prompt(question: str, sections: list[dict]) -> str
    # Numbers sections [1]–[5], same pattern as build_market_prompt().
    # No alias_hint — macro queries are never about a specific company.
```

---

## Query Pipeline Changes (`app/routers/query.py`)

### Macro intent branch
```
detect_intent → "macro"
  → get_macro_data()
  → format_macro_sections()
  → build_macro_prompt()
  → chat_completion(BCB_SYSTEM_PROMPT, ...)
  → return QueryResponse(chunks=_build_bcb_chunks(sections))
```

`_build_bcb_chunks()` mirrors `_build_market_chunks()`: one `ChunkResult` per section, `filename = "BCB — {section}"`, `page_number = 0`, `score = 1.0`.

### Enrichment (RAG and market branches)
1. Call `get_macro_snapshot_line()` — reads from cache, near-zero latency.
2. Append as a numbered section to the prompt: `[N] Contexto Macroeconômico (BCB):\n{snapshot_line}` where N = `len(other_sections) + 1`.
3. After `chat_completion`, check if `f"[{N}]"` appears in the answer text.
4. If yes → append `ChunkResult(filename="BCB — Macro Snapshot", text=snapshot_line, page_number=0, score=1.0)` to the chunks list.
5. If no → omit the BCB chunk entirely (no chip shown, no dead citation link).

---

## Frontend Changes (`app/static/index.html`)

**New badge** — shown when all response chunks are BCB sources:
```css
.grounded-badge.bcb {
    background: #0a2a2a;
    color: #34d399;
    border: 1px solid #0d5a3a;
}
```
Detection: `chunks.every(c => c.filename.startsWith("BCB"))`.

**Chips and ref panel** — no changes needed. `page_number = 0` already supported (Yahoo Finance work). `filename = "BCB — Política Monetária"` renders correctly as-is.

---

## Persistence

`data/bcb_cache.json` — new file, gitignored (runtime data, same treatment as `data/persist/`).

---

## New Test Script

`test_bcb_client.py` — standalone async script (same pattern as `test_market_data.py`):
- `test_fetch_series()` — asserts 12 readings returned per series, correct keys
- `test_focus_report()` — asserts forecast data present for all 4 indicators
- `test_cache()` — writes cache, reads it back, verifies TTL logic
- `test_snapshot_line()` — asserts all 6 indicator labels present in output
- `test_format_sections()` — asserts 5 sections returned with correct names

---

## Files Changed

| File | Change |
|---|---|
| `app/scraper/bcb_client.py` | **New** |
| `app/query/intent.py` | Add `"macro"` intent + examples |
| `app/generation/prompts.py` | Add `BCB_SYSTEM_PROMPT`, `build_macro_prompt()` |
| `app/routers/query.py` | Add macro branch + enrichment injection in RAG/market branches |
| `app/static/index.html` | Add `.grounded-badge.bcb` CSS + badge detection logic |
| `data/bcb_cache.json` | **New** (runtime, gitignored) |
| `test_bcb_client.py` | **New** |
| `.gitignore` | Add `data/bcb_cache.json` |
